from contextlib import contextmanager
from functools import wraps
import logging
import os
import sys
import urllib3

'''
Guidance on logging/printing in the agent:

stdout (e.g., print() output) is for the customer/operator; it should be used when
we have customer sensitive data that they do not want to submit to jellyfish (i.e. passwords)

logger output is for Jellyfish and customers; it should include:
 - function entry/exit timings
 - loop iteration timings/liveness indicators
 - errors/warnings encountered

logger output is submitted to Jellyfish, so should only contain non-sensitive data:
e.g., function names, iteration counts
'''

LOG_FILE_NAME = 'jf_agent.log'

# For styling in log files, I think it's best to always use new lines even when we use
# the special character to ignore them. Leverage always_use_newlines for this
def _emit_helper(_self: logging.Handler, record: logging.LogRecord, always_use_newlines=False):
    try:
        special_code = '[!n]'

        if _self.stream is None:
            if isinstance(_self, logging.FileHandler) and (_self.mode != 'w' or not _self._closed):
                _self.stream = _self._open()

        msg = _self.format(record)
        if special_code in msg:
            msg = msg.replace('[!n]', '')
            if always_use_newlines:
                msg += '\n'
        else:
            msg += '\n'

        _self.stream.write(msg)
        _self.flush()
    except RecursionError:  # See issue 36272
        raise
    except Exception:
        _self.handleError(record)


class CustomStreamHandler(logging.StreamHandler):
    """Handler that controls the writing of the newline character"""

    def emit(self, record) -> None:
        _emit_helper(self, record)


class CustomFileHandler(logging.FileHandler):
    """Handler that controls the writing of the newline character"""

    def emit(self, record) -> None:
        _emit_helper(self, record, always_use_newlines=True)


def configure(outdir):

    # Send log messages to std using a stream handler
    # All INFO level and above errors will go to STDOUT
    stdout_handler = CustomStreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(logging.Formatter(fmt='%(message)s'))
    stdout_handler.setLevel(logging.INFO)

    # Send log messages to logger, using more structured format
    # All DEBUG level and above errors will go to the log file
    logfile_handler = CustomFileHandler(os.path.join(outdir, LOG_FILE_NAME), mode='a')
    logfile_handler.setFormatter(
        logging.Formatter(fmt='%(asctime)s %(threadName)s %(levelname)s %(name)s %(message)s')
    )
    logfile_handler.setLevel(logging.DEBUG)

    # Silence the urllib3 logger to only emit WARNING level logs,
    # because the debug level logs are super noisy
    logging.getLogger(urllib3.__name__).setLevel(logging.WARNING)

    logging.basicConfig(
        level=logging.DEBUG, datefmt='%Y-%m-%d %H:%M:%S', handlers=[logfile_handler, stdout_handler]
    )


def log_entry_exit(logger: logging.Logger, *args, **kwargs):
    def actual_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            logger.debug(f'{func_name}: Starting')
            ret = func(*args, **kwargs)
            logger.debug(f'{func_name}: Ending')
            return ret

        return wrapper

    return actual_decorator


@contextmanager
def log_loop_iters(
    logger, loop_desc, this_iternum, log_every_n_iters, log_entry=True, log_exit=True
):
    if (this_iternum - 1) % log_every_n_iters == 0:
        if log_entry:
            logger.debug(f'Loop "{loop_desc}", iter {this_iternum}: Starting')
        yield
        if log_exit:
            logger.debug(f'Loop "{loop_desc}", iter {this_iternum}: Ending')
    else:
        yield


# Mapping of error/warning codes to templated error messages to be called by
# log_standard_error(). This allows for Jellyfish to better categorize errors/warnings.
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
    3071: 'Jira rate limit exceeded on func {}, retry {} / {}.  Trying again in {}...',
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
    2203: "ERROR: Failed downloading sprints for Jira board: {} with s_start_at={}.\nReceived 400 response:\n{}",
}


def generate_standard_error_msg(error_code, msg_args=[]):
    return f'[{error_code}] {ERROR_MESSAGES.get(error_code).format(*msg_args)}'


def log_standard_error(logger, level, error_code, msg_args=[], exc_info=False):
    '''
    For a failure that should be sent to the logger with an error_code, and also written
    to stdout (for user visibility)
    '''
    assert level >= logging.WARNING
    msg = generate_standard_error_msg(error_code=error_code, msg_args=msg_args)
    logger.log(level=level, msg=msg)
