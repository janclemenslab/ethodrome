# -*- coding: utf-8 -*-
import threading
import time
import numpy as np
import logging
from ..utils.log_exceptions import for_all_methods, log_exceptions
from typing import Optional, List

try:
    import PyDAQmx as daq
    from PyDAQmx.DAQmxCallBack import *
    from PyDAQmx.DAQmxConstants import *
    from PyDAQmx.DAQmxFunctions import *

    pydaqmx_import_error = None
except (ImportError, NotImplementedError) as pydaqmx_import_error:
    pass


logger = logging.getLogger(__name__)


@for_all_methods(log_exceptions(logger))
class IOTask(daq.Task):
    def __init__(
        self,
        dev_name="Dev1",
        cha_name=["ai0"],
        limits=None,
        rate=10000.0,
        nb_inputsamples_per_cycle=None,
        clock_source=None,
        terminals: Optional[List[str]] = None,
        logger=None,
    ):
        """[summary]

        Args:
            dev_name (str, optional): [description]. Defaults to "Dev1".
            cha_name (list, optional): [description]. Defaults to ["ai0"].
            limits (float, optional): [description]. Defaults to 10.0.
            rate (float, optional): [description]. Defaults to 10000.0.
            nb_inputsamples_per_cycle ([type], optional): [description]. Defaults to None.
            clock_source (str, optional): None for AI-synced clock.
                                          Use 'OnboardClock' for boards that don't support this (USB-DAQ).
                                          Defaults to None.
            terminals (List[str], optional):


        Raises:
            TypeError: [description]
            ValueError: [description]
        """
        if pydaqmx_import_error is not None:
            raise pydaqmx_import_error

        self.log = logger

        # check inputs
        daq.Task.__init__(self)
        if not isinstance(cha_name, (list, tuple)):
            raise TypeError(f"`cha_name` is {type(cha_name)}. Should be `list` or `tuple`")

        self.samples_read = daq.int32()

        # channels
        cha_types = {
            "ai": "analog_input",
            "ao": "analog_output",
            "po": "digital_output",
        }
        self.cha_type = [cha_types[cha[:2]] for cha in cha_name]
        if len(set(self.cha_type)) > 1:
            raise ValueError("channels should all be of the same type but are {0}.".format(set(self.cha_type)))

        # TODO: expand "ai0:2" to ["ai0", "ai1", "ai2"]

        self.cha_names = [dev_name + "/" + ch for ch in cha_name]  # prepend device name
        self.cha_string = ", ".join(self.cha_names)
        self.num_channels = len(self.cha_names)
        if nb_inputsamples_per_cycle is None:
            nb_inputsamples_per_cycle = int(rate)

        # terminals
        terminal_types = {
            "RSE": daq.DAQmx_Val_RSE,
            "NRSE": daq.DAQmx_Val_NRSE,
            "Diff": daq.DAQmx_Val_Diff,
            None: daq.DAQmx_Val_RSE,  # default
        }
        if terminals is None:  # default
            terminals = ["RSE" for _ in self.cha_names]
        elif len(terminals) != len(self.cha_names):
            raise ValueError("need term for each channel")
        self.cha_terminals = [terminal_types[terminal] for terminal in terminals]

        # limits
        if limits is None:  # default
            self.cha_limits = [[-10.0, 10.0] for _ in self.cha_names]
        elif isinstance(limits, (float, int)):  # single number
            self.cha_limits = [[-limits, limits] for _ in self.cha_names]
        else:
            self.cha_limits = limits

        if len(self.cha_limits) != len(self.cha_names) or not all([len(limit) == 2 for limit in self.cha_limits]):
            raise ValueError("need term for each channel")

        # FIX: input and output tasks can have different sizes
        self.callback = None
        self.data_gen = None  # called at start of callback
        self.data_rec = None  # called at end of callback

        if len(set(self.cha_type)) != 1:
            raise ValueError("Mixed channel types (AI, AO, DI, DO).")

        if self.cha_type[0] == "analog_input":
            # add all channels
            for name, terminal, limit in zip(self.cha_names, self.cha_terminals, self.cha_limits):
                self.CreateAIVoltageChan(
                    name,
                    "",
                    terminal,
                    limit[0],
                    limit[1],
                    daq.DAQmx_Val_Volts,
                    None,
                )
            self.num_samples_per_chan = nb_inputsamples_per_cycle
            self.num_samples_per_event = nb_inputsamples_per_cycle  # self.num_samples_per_chan*self.num_channels
            self.AutoRegisterEveryNSamplesEvent(daq.DAQmx_Val_Acquired_Into_Buffer, self.num_samples_per_event, 0)
            self.CfgInputBuffer(self.num_samples_per_chan * self.num_channels * 4)
        elif self.cha_type[0] == "analog_output":
            for name, terminal, limit in zip(self.cha_names, self.cha_terminals, self.cha_limits):
                self.CreateAOVoltageChan(
                    name,
                    "",
                    limit[0],
                    limit[1],
                    daq.DAQmx_Val_Volts,
                    None,
                )
            self.num_samples_per_chan = 5000
            self.num_samples_per_event = 1000  # determines shortest interval at which new data can be generated
            self.AutoRegisterEveryNSamplesEvent(daq.DAQmx_Val_Transferred_From_Buffer, self.num_samples_per_event, 0)
            self.CfgOutputBuffer(self.num_samples_per_chan * self.num_channels * 2)
            # ensures continuous output and avoids collision of old and new data in buffer
            self.SetWriteRegenMode(daq.DAQmx_Val_DoNotAllowRegen)
        elif self.cha_type[0] == "digital_output":
            for name in self.cha_names:
                self.CreateDOChan(name, "", daq.DAQmx_Val_ChanPerLine)
            self.num_samples_per_chan = 5000
            self.num_samples_per_event = 1000  # determines shortest interval at which new data can be generated
            self.AutoRegisterEveryNSamplesEvent(daq.DAQmx_Val_Transferred_From_Buffer, self.num_samples_per_event, 0)
            self.CfgOutputBuffer(self.num_samples_per_chan * self.num_channels * 2)
            # ensures continuous output and avoids collision of old and new data in buffer
            self.SetWriteRegenMode(daq.DAQmx_Val_DoNotAllowRegen)

        if "digital" in self.cha_type[0]:
            self._data = np.zeros((self.num_samples_per_chan, self.num_channels), dtype=np.uint8)  # init empty data array
        else:
            self._data = np.zeros((self.num_samples_per_chan, self.num_channels), dtype=np.float64)  # init empty data array

        # set up trigger for output channels and clock
        if "output" in self.cha_type[0]:
            if clock_source is None:
                clock_source = "ai/SampleClock"  # use clock of analog input
                self.CfgDigEdgeStartTrig("ai/StartTrigger", daq.DAQmx_Val_Rising)
            else:
                self.DisableStartTrig()
        else:  # analog input
            clock_source = "OnboardClock"  # use internal clock

        self.CfgSampClkTiming(
            clock_source,
            rate,
            daq.DAQmx_Val_Rising,
            daq.DAQmx_Val_ContSamps,
            self.num_samples_per_chan,
        )

        self.AutoRegisterDoneEvent(0)
        self._data_lock = threading.Lock()
        self._newdata_event = threading.Event()
        if "output" in self.cha_type[0]:
            self.EveryNCallback()

    def __repr__(self):
        return "{0}: {1}".format(self.cha_type[0], self.cha_string)

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
        # for clean teardown, catch PyDAQmx.DAQmxFunctions.GenStoppedToPreventRegenOfOldSamplesError
        with self._data_lock:
            systemtime = time.time()
            if self.data_gen is not None:
                try:
                    self._data = next(self.data_gen)  # get data from data generator
                    self.log.warning(f"datagen {self.data_gen}")
                except StopIteration as e:
                    self.log.warning(f"datagen {self.data_gen} StopIteration {e}")
                    self._data = None

            if self.cha_type[0] == "analog_input":
                # should only read self.num_samples_per_event!! otherwise recordings will be zeropadded for each chunk
                self.ReadAnalogF64(
                    daq.DAQmx_Val_Auto,
                    1.0,
                    daq.DAQmx_Val_GroupByScanNumber,
                    self._data,
                    self.num_samples_per_chan * self.num_channels,
                    daq.byref(self.samples_read),
                    None,
                )
                # only keep samples that were actually read, .value converts c_long to int
                self._data = self._data[: self.samples_read.value, :]

            elif self.cha_type[0] == "analog_output" and self._data is not None:
                self.WriteAnalogF64(
                    self._data.shape[0],
                    0,
                    daq.DAQmx_Val_WaitInfinitely,
                    daq.DAQmx_Val_GroupByScanNumber,
                    self._data,
                    daq.byref(self.samples_read),
                    None,
                )
            elif self.cha_type[0] == "digital_output" and self._data is not None:
                self.WriteDigitalLines(
                    self._data.shape[0],
                    0,
                    daq.DAQmx_Val_WaitInfinitely,
                    daq.DAQmx_Val_GroupByScanNumber,
                    self._data,
                    daq.byref(self.samples_read),
                    None,
                )

            if self.data_rec is not None:
                for data_rec in self.data_rec:
                    if self._data is not None:
                        self.log.warning(f"{data_rec} {systemtime}")
                        data_rec.send((self._data, systemtime))
            self._newdata_event.set()

        return 0  # The function should return an integer

    def DoneCallback(self, status):
        """Call when Task is stopped/done."""
        self.log.warning("Done status", status)
        return 0  # The function should return an integer


def coroutine(func):
    """decorator that auto-initializes (calls `next(None)`) coroutines"""

    def start(*args, **kwargs):
        cr = func(*args, **kwargs)
        next(cr)
        return cr

    return start


@coroutine
def data_playlist(sounds, play_order, playlist_info=None, logger=None, name="standard"):
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
                    if logger:
                        logger.warning(msg)
            stim = sounds[pp]
            yield stim
    except (GeneratorExit, StopIteration):
        if logger is not None:
            logger.warning(f"   {name} cleaning up datagen.")


def _format_playlist(playlist, cnt):
    string = f"cnt: {cnt}; "
    for key, val in playlist.items():
        string += f"{key}: {val}; "
    return string
