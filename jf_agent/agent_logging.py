from datetime import datetime
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

Since we generally want errors/warnings to go to BOTH stdout and the logger, we
should generally use log_and_print() instead of logger.whatever().
'''

LOG_FILE_NAME = 'jf_agent.log'


def configure(outdir):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(threadName)s %(levelname)s %(name)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        filename=os.path.join(outdir, LOG_FILE_NAME),
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


# Mapping of error/warning codes to templated error messages to be called by
# log_and_print_error_or_warning(). This allows for Jellyfish to better categorize errors/warnings.
ERROR_MESSAGES = {
    0000: 'An unknown error has occurred. Error message: {}',
    3000: 'Failed to upload file {} to S3 bucket',
    3001: 'Connection to bucket was disconnected while uploading: {} with the following error: {}. Retrying...',
    3010: 'Rate limiter: thought we were operating within our limit (made {}/{} calls for {}), but got HTTP 429 anyway!',
    3020: 'Next available time to make call is after the timeout of {} seconds. Giving up.',
    3030: 'ERROR: Could not parse response with status code {}. Contact an administrator for help.',
    3011: 'Error normalizing PR {} from repo {}. Skipping...',
    3021: 'Error getting PRs for repo {}. Skipping...',
    3031: 'Unable to parse the diff For PR {} in repo {}; proceeding as though no files were changed.',
    3041: 'For PR {} in repo {}, caught HTTPError (HTTP 401) when attempting to retrieve changes; '
    'proceeding as though no files were changed',
    3051: 'For PR {} in repo {}, caught UnicodeDecodeError when attempting to decode changes; proceeding as though no files were changed',
    3061: 'Failed to download {} data:\n{}',
    3081: 'Got unexpected HTTP 403 for repo {}.  Skipping...',
    3091: 'Github rate limit exceeded.  Trying again in {}...',
    3101: 'Request to {} has failed {} times -- giving up!',
    3121: 'Got HTTP {} when fetching commit {} for "{}", this likely means you are trying to fetch an invalid re',
    3131: 'Got {} {} when {} ({})',
    3141: 'Got {} {} when {}',
    3151: 'Getting HTTP 429s for over an hour; giving up!',
    3002: 'Failed to download jira data:\n{}',
    3012: 'Caught KeyError from search_issues(), reducing batch size to {}',
    3022: 'Caught KeyError from search_issues(), batch size is already 0, bailing out',
    3032: 'Exception encountered in thread {}\n{}',
    3042: '[Thread {}] Jira issue downloader FAILED',
    3052: 'JIRAError ({}), reducing batch size to {}',
    3062: 'Apparently unable to fetch issue based on search_params {}',
    3072: 'Error calling createmeta JIRA endpoint',
    3082: 'OJ-9084: Changelog history item with no \'fieldId\' or \'field\' key: {}',
    3092: (
        'OJ-22511: server side 500, batch size reduced to 0, '
        'last error was: {} with jql: {}, start_at: {}, and batch_size: {}. Skipping one issue ahead...'
    ),
    2101: 'Failed to connect to {}:\n{}',
    2102: 'Unable to access project {}, may be a Jira misconfiguration. Skipping...',
    2112: 'Failed to connect to Jira for project ID \n{}',
    2122: 'you do not have the required \'development field\' permissions in jira required to scan for missing repos',
    2132: (
        'Missing recommended jira_fields! For the best possible experience, '
        'please add the following to `include_fields` in the '
        'configuration file: {}'
    ),
    2142: (
        'Excluding recommended jira_fields! For the best possible experience, '
        'please remove the following from `exclude_fields` in the '
        'configuration file: {}',
    ),
    2201: (
        '\nERROR: Failed to download ({}) repo(s) from the group {}. '
        'Please check that the appropriate permissions are set for the following repos... ({})'
    ),
    2202: "You do not have the required permissions in jira required to fetch boards for the project {}",
}


def log_and_print(logger, level, msg):
    '''
    For an info-level message that should be sent to the logger, and also written
    to stdout (for user visibility)
    '''
    logger.log(level, msg)
    print(msg, flush=True)


def log_and_print_error_or_warning(logger, level, error_code, msg_args=[], exc_info=False):
    '''
    For a failure that should be sent to the logger with an error_code, and also written
    to stdout (for user visibility)
    '''
    assert level >= logging.WARNING
    msg = f'[{error_code}] {ERROR_MESSAGES.get(error_code).format(*msg_args)}'
    logger.log(level, msg, exc_info=exc_info)
    print(msg, flush=True)
    if exc_info:
        print(traceback.format_exc())


# TODO(asm,2021-08-12): This is sloppy, we should figure out a way to
# get Python's native logging to behave the way we want.
def verbose(msg):
    print(f"[{datetime.now().isoformat()}] {msg}")
