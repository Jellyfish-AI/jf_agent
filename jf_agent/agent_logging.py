from contextlib import contextmanager
from functools import wraps
import logging
import os
import traceback

'''
Guidance on logging/printing in the agent:

stdout (e.g., print() output) is for the customer/operator; it should include
 - info on run progress: tqdm output, etc.
 - errors/warnings encountered

logger output is for Jellyfish; it should include
 - function entry/exit timings
 - loop iteration timings/liveness indicators
 - errors/warnings encountered

logger output is submitted to Jellyfish, so should only contain non-sensitive data:
e.g., function names, iteration counts
'''


def configure(outdir):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(threadName)s %(levelname)s %(name)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        filename=os.path.join(outdir, 'jf_agent.log'),
        filemode='a',  # May be adding to a file created in a previous run
    )


def log_entry_exit(logger, *args, **kwargs):
    def actual_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            logger.info(f'{func_name}: Starting')
            ret = func(*args, **kwargs)
            logger.info(f'{func_name}: Ending')
            return ret

        return wrapper

    return actual_decorator


@contextmanager
def log_loop_iters(
    logger, loop_desc, this_iternum, log_every_n_iters, log_entry=True, log_exit=True
):
    if (this_iternum - 1) % log_every_n_iters == 0:
        if log_entry:
            logger.info(f'Loop "{loop_desc}", iter {this_iternum}: Starting')
        yield
        if log_exit:
            logger.info(f'Loop "{loop_desc}", iter {this_iternum}: Ending')
    else:
        yield


def log_and_print(logger, level, msg, exc_info=False):
    '''
    For a failure that should be sent to the logger, and also written
    to stdout (for user visibility)
    '''
    logger.log(level, msg, exc_info=exc_info)
    print(msg)
    if exc_info:
        print(traceback.format_exc())
