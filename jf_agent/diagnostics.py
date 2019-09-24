from datetime import datetime
from functools import wraps
import json
import os
import pytz
import subprocess


DIAGNOSTICS_FILE = None


def _write_diagnostic(obj):
    json.dump(obj, DIAGNOSTICS_FILE)
    DIAGNOSTICS_FILE.flush()


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
