from collections import namedtuple
from datetime import datetime, timedelta
from functools import wraps
import json
import os
import pytz
import shutil
import subprocess
import threading
import time

import psutil


DIAGNOSTICS_FILE = None


def _write_diagnostic(obj):
    if not DIAGNOSTICS_FILE:
        return

    json.dump(obj, DIAGNOSTICS_FILE)
    DIAGNOSTICS_FILE.write('\n')  # facilitate parsing
    DIAGNOSTICS_FILE.flush()


def capture_agent_version():
    git_head_hash = os.getenv('SHA')
    build_timestamp = os.getenv('BUILDTIME')
    _write_diagnostic({'type': 'agent_version', 'sha': git_head_hash, 'timestamp': build_timestamp})


def capture_timing(*args, **kwargs):
    def actual_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = pytz.utc.localize(datetime.utcnow()).isoformat()
            if 'func_name_override' in kwargs:
                func_name = kwargs.pop('func_name_override')
            else:
                func_name = func.__name__
            ret = func(*args, **kwargs)
            end_time = pytz.utc.localize(datetime.utcnow()).isoformat()
            diag_obj = {
                'type': 'func_timing',
                'name': func_name,
                'start': start_time,
                'end': end_time,
            }
            if ret is not None:
                try:
                    diag_obj['num_items'] = len(ret)
                except TypeError:
                    if type(ret) is int:
                        diag_obj['num_items'] = ret
            _write_diagnostic(diag_obj)
            return ret

        return wrapper

    return actual_decorator


def capture_run_args(run_mode, config_file, outdir, prev_output_dir):
    _write_diagnostic(
        {
            'type': 'run_args',
            'run_mode': run_mode,
            'config_file': config_file,
            'outdir': outdir,
            'prev_output_dir': prev_output_dir,
        }
    )


def capture_outdir_size(outdir):
    _write_diagnostic(
        {
            'type': 'outdir_size',
            'size_kb': int(
                subprocess.check_output(['du', '-sk', outdir]).split()[0].decode('utf-8')
            ),
        }
    )


def capture_download_data_summary(filenames_to_summarize):
    for filename in filenames_to_summarize:
        with open(filename) as data_file:
            data = json.loads(data_file)
            _write_diagnostic(
                {
                    'type': 'data_download_summary',
                    'data_type': {filename},
                    'num_items_downloaded': len(data),
                }
            )


def continually_gather_system_diagnostics(kill_event, outdir):
    def _flush_cached_readings(cached_readings):
        if not cached_readings:
            return
        _write_diagnostic(
            {
                'type': 'sys_resources_60s',
                'start': pytz.utc.localize(cached_readings[0].time).isoformat(),
                'end': pytz.utc.localize(cached_readings[-1].time).isoformat(),
                'cpu_pct': ['%.2f' % r.cpu_pct for r in cached_readings],
                'mem_used_gb': ['%.2f' % r.memory_used_gb for r in cached_readings],
                'mem_pct': ['%.2f' % r.memory_pct for r in cached_readings],
                'disk_used_gb': ['%.2f' % r.disk_used_gb for r in cached_readings],
                'disk_pct': ['%.2f' % r.disk_pct for r in cached_readings],
            }
        )

    SysReading = namedtuple(
        'SysReading',
        ('time', 'cpu_pct', 'memory_used_gb', 'memory_pct', 'disk_used_gb', 'disk_pct'),
    )

    now = datetime.utcnow()
    readings = threading.local()
    readings.cached_readings = []
    readings.last_reading_time = None
    readings.last_flush_time = now

    while True:
        if kill_event.is_set():
            _flush_cached_readings(readings.cached_readings)
            readings.cached_readings = []
            return
        else:
            now = datetime.utcnow()
            if not readings.last_reading_time or (now - readings.last_reading_time) > timedelta(
                seconds=60
            ):
                cpu = psutil.cpu_percent()
                memory = psutil.virtual_memory()
                disk = shutil.disk_usage(outdir)
                readings.cached_readings.append(
                    SysReading(
                        now,
                        cpu / 100,
                        (memory.total - memory.available) / (1024 ** 3),
                        (memory.total - memory.available) / memory.total,
                        disk.used / (1024 ** 3),
                        disk.used / disk.total,
                    )
                )
                readings.last_reading_time = now

            if now - readings.last_flush_time > timedelta(seconds=300):
                _flush_cached_readings(readings.cached_readings)
                readings.cached_readings = []
                readings.last_flush_time = now

            # Keep the sleep short so that the thread's responsive to the kill_event
            time.sleep(1)


def open_file(outdir):
    global DIAGNOSTICS_FILE
    assert DIAGNOSTICS_FILE is None
    DIAGNOSTICS_FILE = open(os.path.join(outdir, 'diagnostics.json'), 'w')


def close_file():
    try:
        global DIAGNOSTICS_FILE
        DIAGNOSTICS_FILE.close
    except Exception:
        pass
