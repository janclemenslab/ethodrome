import time
import numpy as np
import pandas as pd
import subprocess
import defopt
from itertools import cycle

from ethomaster import config
from ethomaster.head.ZeroClient import ZeroClient
from ethomaster.utils.sound import *
from ethomaster.utils.config import readconfig
from ethoservice.DAQZeroService import DAQ
from ethoservice.NITriggerZeroService import NIT


def trigger(trigger_name):
    ip_address = 'localhost'
    port = "/Dev1/port0/line1:3"
    trigger_types = {'START': [1, 0, 0],
                     'STOP': [0, 1, 0],
                     'NEXT': [0, 0, 1],
                     'NULL': [0, 0, 0]}
    print([NIT.SERVICE_PORT, NIT.SERVICE_NAME])
    nit = ZeroClient("{0}".format(ip_address), 'nidaq')
    try:
        sp = subprocess.Popen('python -m ethoservice.NITriggerZeroService')
        nit.connect("tcp://{0}:{1}".format(ip_address, NIT.SERVICE_PORT))
        print('done')
        nit.setup(-1, port)
        # nit.init_local_logger('{0}/{1}_nit.log'.format(dirname, filename))
        print('sending START')
        nit.send_trigger(trigger_types[trigger_name])
    except:
        pass
    nit.finish()
    nit.stop_server()
    del(nit)
    sp.terminate()
    sp.kill()


def clientcc(filename: str, filecounter: int, protocolfile: str, playlistfile: str, save: bool=False):
    # load config/protocols
    print(filename)
    print(filecounter)
    print(protocolfile)
    print(playlistfile)
    print(save)
    prot = readconfig(protocolfile)
    maxduration = int(prot['NODE']['maxduration'])
    user_name = prot['NODE']['user']
    folder_name = prot['NODE']['folder']

    ip_address = 'localhost'

    # unique file name for video and node-local logs
    # if filename is None:
    #     filename = '{0}-{1}'.format(ip_address, time.strftime('%Y%m%d_%H%M%S'))
    # dirname = prot['NODE']['savefolder']
    print(filename)

    trigger('START')
    print('sent START')
    SER = 'pickle'
    daq_server_name = 'python -m {0} {1}'.format(DAQ.__module__, SER)
    daq_service_port = DAQ.SERVICE_PORT

    fs = int(prot['DAQ']['samplingrate'])
    shuffle_playback = eval(prot['DAQ']['shuffle'])
    # load playlist, sounds, and enumerate play order
    playlist = pd.read_table(playlistfile, dtype=None, delimiter='\t')
    sounds = load_sounds(playlist, fs, attenuation=config['ATTENUATION'],
                LEDamp=prot['DAQ']['ledamp'], stimfolder=config['HEAD']['stimfolder'],
                mirrorsound=bool(int(prot['NODE'].get('mirrorsound', 1))),
                cast2int=False, aslist=False)
    playlist_items, totallen = build_playlist(sounds, maxduration, fs, shuffle=shuffle_playback)
    # THIS SHOULD HAPPEN OUTSIDE OF THE SERVICE!!
    # get digital pattern from analog_data_out - duplicate analog_data_out, add next trigger at beginning of each sound
    triggers = list()
    for sound in sounds:
        this_trigger = np.zeros((sound.shape[0], len(prot['DAQ'].get('digital_chans_out', []))), dtype=np.uint8)
        # if len(np_triggers) == 0:
        #     this_trigger[:5, 0] = 1  # START on first
        if len(triggers) == len(sounds)-1:
            this_trigger[-5:, 1] = 1  # STOP on last
        else:
            this_trigger[:5, 2] = 1  # NEXT
        this_trigger[:5, 2] = 1  # NEXT
        triggers.append(this_trigger.astype(np.uint8))

    if maxduration == -1:
        print(f'setting maxduration from playlist to {totallen}.')
        maxduration = totallen
        playlist_items = cycle(playlist_items)  # iter(playlist_items)
    else:
        playlist_items = cycle(playlist_items)

    if not isinstance(prot['DAQ']['analog_chans_in'], list):
        prot['DAQ']['analog_chans_in'] = [prot['DAQ']['analog_chans_in']]
    if not isinstance(prot['DAQ']['analog_chans_out'], list):
        prot['DAQ']['analog_chans_out'] = [prot['DAQ']['analog_chans_out']]
    # send START trigger here
    print([DAQ.SERVICE_PORT, DAQ.SERVICE_NAME])
    daq = ZeroClient("{0}@{1}".format(user_name, ip_address), 'nidaq', serializer=SER)
    sp = subprocess.Popen(daq_server_name, creationflags=subprocess.CREATE_NEW_CONSOLE)
    daq.connect("tcp://{0}:{1}".format(ip_address, daq_service_port))
    print('done')
    print('sending sound data to {0} - may take a while.'.format(ip_address))
    if save:
        daq_save_filename = '{0}_daq_test.h5'.format(filename)
    else:
        daq_save_filename = None

    daq.setup(daq_save_filename, playlist_items, maxduration, fs, eval(prot['DAQ']['display']),
              analog_chans_out=prot['DAQ'].get('analog_chans_out', []),
              analog_chans_in=prot['DAQ'].get('analog_chans_in', []),
              digital_chans_out=prot['DAQ'].get('digital_chans_out', []),
              analog_data_out=sounds,
              digital_data_out=triggers)
    if save:
        daq.init_local_logger('{0}_daq.log'.format(filename))

    daq.start()
    t0 = time.clock()
    while daq.is_busy():
        time.sleep(1)
        t1 = time.clock()
        print(f'   Busy {t1-t0:1.2f} seconds.\r', end='', flush=True)

    # send STOP trigger here
    trigger('STOP')
    print('sent STOP')

    sp.terminate()
    sp.kill()


if __name__ == '__main__':
    defopt.run(clientcc)
