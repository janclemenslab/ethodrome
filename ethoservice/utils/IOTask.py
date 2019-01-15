# -*- coding: utf-8 -*-
import PyDAQmx as daq
from PyDAQmx.DAQmxCallBack import *
from PyDAQmx.DAQmxConstants import *
from PyDAQmx.DAQmxFunctions import *

import threading
import sys
import time
import numpy as np
import h5py

# callback specific imports
import matplotlib
matplotlib.use('tkagg')
import matplotlib.pyplot as plt

from .ConcurrentTask import ConcurrentTask

plt.ion()


class IOTask(daq.Task):
    """IOTask does X."""

    def __init__(self, dev_name="Dev1", cha_name=["ai0"], limits=10.0, rate=10000.0):
        """Initialize IOTask.

        ARGUMENTS:
        dev_name - ni daqmx device name
        cha_name - list of channels (must be pure - either all ai or ao)
        limits   - voltage limits
        rate     - sampling rate
        """
        # check inputs
        daq.Task.__init__(self)
        if not isinstance(cha_name, (list, tuple)):
            raise TypeError(f'`cha_name` is {type(cha_name)}. Should be `list` or `tuple`')

        self.samples_read = daq.int32()
        cha_types = {"ai": "analog_input", "ao": "analog_output", 'po': 'digital_output'}
        self.cha_type = [cha_types[cha[:2]] for cha in cha_name]
        if len(set(self.cha_type)) > 1:
            raise ValueError('channels should all be of the same type but are {0}.'.format(set(self.cha_type)))

        self.cha_name = [dev_name + '/' + ch for ch in cha_name]  # append device name
        self.cha_string = ", ".join(self.cha_name)
        self.num_channels = len(cha_name)

        # FIX: input and output tasks can have different sizes
        self.callback = None
        self.data_gen = None  # called at start of callback
        self.data_rec = None  # called at end of callback
        if self.cha_type[0] is "analog_input":
            self.num_samples_per_chan = 10000
            self.num_samples_per_event = 10000  # self.num_samples_per_chan*self.num_channels
            self.CreateAIVoltageChan(self.cha_string, "", DAQmx_Val_RSE, -limits, limits, DAQmx_Val_Volts, None)
            self.AutoRegisterEveryNSamplesEvent(DAQmx_Val_Acquired_Into_Buffer, self.num_samples_per_event, 0)
            self.CfgInputBuffer(self.num_samples_per_chan * self.num_channels * 4)
            clock_source = 'OnboardClock'#ao/SampleClock'  # None  # use internal clock
        elif self.cha_type[0] is "analog_output":
            self.num_samples_per_chan = 5000
            self.num_samples_per_event = 1000  # determines shortest interval at which new data can be generated
            self.CreateAOVoltageChan(self.cha_string, "", -limits, limits, DAQmx_Val_Volts, None)
            self.AutoRegisterEveryNSamplesEvent(DAQmx_Val_Transferred_From_Buffer, self.num_samples_per_event, 0)
            self.CfgOutputBuffer(self.num_samples_per_chan * self.num_channels * 2)
            # self.CfgOutputBuffer(self.num_samples_per_chan)
            # ensures continuous output and avoids collision of old and new data in buffer
            self.SetWriteRegenMode(DAQmx_Val_DoNotAllowRegen)
            clock_source = 'ai/SampleClock'# 'OnboardClock'  # None  # use internal clock
        elif self.cha_type[0] is "digital_output":
            self.num_samples_per_chan = 5000
            self.num_samples_per_event = 1000  # determines shortest interval at which new data can be generated
            self.CreateDOChan(self.cha_string, "", DAQmx_Val_ChanPerLine)
            self.AutoRegisterEveryNSamplesEvent(DAQmx_Val_Transferred_From_Buffer, self.num_samples_per_event, 0)
            self.CfgOutputBuffer(self.num_samples_per_chan * self.num_channels * 2)
            # ensures continuous output and avoids collision of old and new data in buffer
            self.SetWriteRegenMode(DAQmx_Val_DoNotAllowRegen)
            clock_source = 'ai/SampleClock'  # None  # use internal clock

        if 'digital' in self. cha_type[0]:
            self._data = np.zeros((self.num_samples_per_chan, self.num_channels), dtype=np.uint8)  # init empty data array
        else:
            self._data = np.zeros((self.num_samples_per_chan, self.num_channels), dtype=np.float64)  # init empty data array
        self.CfgSampClkTiming(clock_source, rate, DAQmx_Val_Rising, DAQmx_Val_ContSamps, self.num_samples_per_chan)
        self.AutoRegisterDoneEvent(0)
        self._data_lock = threading.Lock()
        self._newdata_event = threading.Event()
        if 'output' in self.cha_type[0]:
            self.EveryNCallback()


    def __repr__(self):
        return '{0}: {1}'.format(self.cha_type[0], self.cha_string)

    def stop(self):
        """Stop DAQ."""
        if self.data_gen is not None:
            self._data = self.data_gen.close()  # close data generator
        if self.data_rec is not None:
            for data_rec in self.data_rec:
                data_rec.send(None)
                data_rec.finish(verbose=True, sleepcycletimeout=2)
                data_rec.close()

    def EveryNCallback(self):
        """Call whenever there is data to be read/written from/to the buffer.

        Calls `self.data_gen` or `self.data_rec` for requesting/processing data.
        """
        with self._data_lock:
            systemtime = time.time()
            if self.data_gen is not None:
                self._data = next(self.data_gen)  # get data from data generator
            if self.cha_type[0] is "analog_input":
                # should only read self.num_samples_per_event!! otherwise recordings will be zeropadded for each chunk
                self.ReadAnalogF64(DAQmx_Val_Auto, 1.0, DAQmx_Val_GroupByScanNumber,
                                   self._data, self.num_samples_per_chan * self.num_channels, daq.byref(self.samples_read), None)
                # only keep samples that were actually read, .value converts c_long to int
                self._data = self._data[:self.samples_read.value, :]

            elif self.cha_type[0] is "analog_output":
                self.WriteAnalogF64(self._data.shape[0], 0, DAQmx_Val_WaitInfinitely, DAQmx_Val_GroupByScanNumber,
                                    self._data, daq.byref(self.samples_read), None)
            elif self.cha_type[0] is 'digital_output':
                self.WriteDigitalLines(self._data.shape[0], 0, DAQmx_Val_WaitInfinitely, DAQmx_Val_GroupByScanNumber,
                                       self._data, daq.byref(self.samples_read), None)

            if self.data_rec is not None:
                for data_rec in self.data_rec:
                    if self._data is not None:
                        data_rec.send((self._data, systemtime))
            self._newdata_event.set()
        return 0  # The function should return an integer

    def DoneCallback(self, status):
        """Call when Task is stopped/done."""
        print("Done status", status)
        return 0  # The function should return an integer


def plot(disp_queue, channels: int=3):
    """Coroutine for plotting.

    Fast, realtime as per: https://gist.github.com/pklaus/62e649be55681961f6c4
    """
    plt.ion()
    fig = plt.figure()
    fig.canvas.set_window_title('traces: daq')
    ax = [fig.add_subplot(channels, 1, channel+1) for channel in range(channels)]
    plt.show(False)
    plt.draw()
    fig.canvas.start_event_loop(0.001)  # otherwise plot freezes after 3-4 iterations
    bgrd = [fig.canvas.copy_from_bbox(this_ax.bbox) for this_ax in ax]
    points = [this_ax.plot(np.arange(10000), np.zeros((10000, 1)))[0] for this_ax in ax] # init plot content
    [this_ax.set_ylim(-5, 5) for this_ax in ax] # init plot content

    RUN = True
    while RUN:
        try:
            if disp_queue.poll(0.1):
                data = disp_queue.recv()
                if data is not None:
                    # print("    plotting {0}".format(data[0].shape))
                    # for chn in range(data[0].shape[1]):
                    nb_samples = data[0].shape[0]
                    x = np.arange(nb_samples)
                    for cnt, chn in enumerate([0, 1, 2, 3, 4]):
                        fig.canvas.restore_region(bgrd[cnt])  # restore background
                        points[cnt].set_data(x, data[0][:nb_samples, chn])
                        ax[cnt].draw_artist(points[cnt])  # redraw just the points
                        fig.canvas.blit(ax[cnt].bbox)  # fill in the axes rectangle
                        # ax[cnt].relim()
                        # ax[cnt].autoscale_view()                 # rescale the y-axis
                    fig.canvas.draw()
                    fig.canvas.flush_events()
                else:
                    RUN = False
        except Exception as e:
                print(e)
    # clean up
    print("   closing plot")
    plt.close(fig)


def save(frame_queue, filename, num_channels=1, attrs=None, sizeincrement=100, start_time=None):
    """Coroutine for saving data."""
    f = h5py.File(filename, "w")

    if attrs is not None:
        for key, val in attrs.items():
            try:
                f.attrs[key] = val
            except (NameError, TypeError):
                f.attrs[key] = str(val)
            f.flush()

    dset_samples = f.create_dataset("samples", shape=[0, num_channels],
                                    maxshape=[None, num_channels], dtype=np.float64, compression="gzip")
    dset_systemtime = f.create_dataset("systemtime", shape=[sizeincrement, 1],
                                       maxshape=[None, 1], dtype=np.float64, compression="gzip")
    dset_samplenumber = f.create_dataset("samplenumber", shape=[sizeincrement, 1],
                                         maxshape=[None, 1], dtype=np.float64, compression="gzip")
    print("opened file \"{0}\".".format(filename))
    framecount = 0
    RUN = True
    while RUN:
        frame_systemtime = frame_queue.get()
        if framecount % sizeincrement == sizeincrement - 1:
            f.flush()
            dset_systemtime.resize(dset_systemtime.shape[0] + sizeincrement, axis=0)
            dset_samplenumber.resize(dset_samplenumber.shape[0] + sizeincrement, axis=0)
        if frame_systemtime is None:
            print("   stopping save")
            RUN = False
        else:
            frame, systemtime = frame_systemtime  # unpack
            if start_time is None:
                start_time = systemtime
            sys.stdout.write("\r   {:1.1f} seconds: saving {} ({})".format(
                             systemtime-start_time, frame.shape, framecount))
            dset_samples.resize(dset_samples.shape[0] + frame.shape[0], axis=0)
            dset_samples[-frame.shape[0]:, :] = frame
            dset_systemtime[framecount, :] = systemtime
            dset_samplenumber[framecount, :] = frame.shape[0]
            framecount += 1
    f.flush()
    f.close()
    print("   closed file \"{0}\".".format(filename))


def log(file_name):
    f = open(file_name, 'r')      # open file
    try:
        while True:
            message = (yield)  # gets sent variables
            f.write(message)  # write log to file
    except GeneratorExit:
        print("   closing file \"{0}\".".format(file_name))
        f.close()  # close file


def coroutine(func):
    """ decorator that auto-initializes (calls `next(None)`) coroutines"""
    def start(*args, **kwargs):
        cr = func(*args, **kwargs)
        next(cr)
        return cr
    return start


@coroutine
def data(channels=1):
    """generator yields next chunk of data for output"""
    # generate all stimuli
    data = list()
    for ii in range(2):
        # t = np.arange(0, 1, 1.0 / max(100.0 ** ii, 100))
        # tmp = np.tile(0.2 * np.sin(5000 * t).astype(np.float64), (channels, 1)).T

        # simple ON/OFF pattern
        tmp = 0 * ii * np.zeros((channels, 10000)).astype(np.float64).T
        data.append(np.ascontiguousarray(tmp))  # `ascont...` necessary since `.T` messes up internal array format
    count = 0  # init counter
    try:
        while True:
            count += 1
            # print("{0}: generating {1}".format(count, data[(count-1) % len(data)].shape))
            yield 0*data[(count - 1) % len(data)]
    except GeneratorExit:
        print("   cleaning up datagen.")


@coroutine
def data_playlist(sounds, play_order, playlist_info=None, logger=None, name='standard'):
    """sounds - list of nparrays"""
    first_run = True
    run_cnt = 0
    playlist_cnt = 0
    try:
        while play_order:
            run_cnt += 1
            # duplicate first stim - otherwise we miss the first in the playlist
            if first_run:
                pp = 0
                first_run = False
            else:
                pp = next(play_order)
                playlist_cnt += 1
                if playlist_info is not None:
                    msg = _format_playlist(playlist_info.loc[pp], playlist_cnt)
                    print(f'\n{msg}')
                    if logger:
                        logger.info(msg)
            stim = sounds[pp]
            yield stim
    except GeneratorExit:
        print(f"   {name} cleaning up datagen.")


def _format_playlist(playlist, cnt):
    string = f'cnt: {cnt}; '
    for key, val in playlist.items():
        string += f'{key}: {val}; '
    return string
