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

ERROR_MESSAGES = {
    3000: 'Failed to upload file {filename} to S3 bucket',
    3010: 'Rate limiter: thought we were operating within our limit (made {calls_made}/{max_calls} calls for {realm}), but got HTTP 429 anyway!',
    3020: 'Next available time to make call is after the timeout of {timeout_secs} seconds. Giving up.',
    3030: 'ERROR: Could not parse response with status code {resp.status_code}. Contact an administrator for help.',
    3001: 'PR {id} doesn\'t reference a source and/or destination repository; skipping it...',
    3011: 'Error normalizing PR {id} from repo {id}. Skipping...',
    3021: 'Error getting PRs for repo {id}. Skipping...',
    3031: 'Unable to parse the diff For PR {id} in repo {id}; proceeding as though no files were changed.',
    3041: 'For PR {id} in repo {id}, caught HTTPError (HTTP 401) when attempting to retrieve changes; ' 'proceeding as though no files were changed',
    3051: 'For PR {id} in repo {id}, caught UnicodeDecodeError when attempting to decode changes; proceeding as though no files were changed',
    3061: 'Failed to download {git_provider} data:\n{e}',
    3071: 'ValueError: {git_provider} is not a supported git_provider for this run_mode',
    3081: 'Got unexpected HTTP 403 for repo {url}.  Skipping...',
    3091: 'Github rate limit exceeded.  Trying again in {reset_wait_str}...',
    3101: 'Request to {url} has failed {i} times -- giving up!',
    3111: 'Expected an array of json results, but got: {page}',
    3121: 'Got HTTP {response_status_code} when fetching commit {ref} for "{full_repo_name}", this likely means you are trying to fetch an invalid re',
    3131: 'Got {error_name} {response_code} when {action} ({e})',
    3141: 'Got {error_name} {response_code} when {action}',
    3151: 'Getting HTTP 429s for over an hour; giving up!',
    3002: 'Failed to download jira data:\n{e}',
    3012: 'Caught KeyError from search_issues(), reducing batch size to {batch_size}',
    3022: 'Caught KeyError from search_issues(), batch size is already 0, bailing out',
    3032: 'Exception encountered in thread {thread_num}\n{traceback.format_exc()}',
    3042: '[Thread {thread_num}] Jira issue downloader FAILED',
    3052: 'JIRAError ({e}), reducing batch size to {batch_size}',
    3062: 'Apparently unable to fetch issue based on search_params {search_params}',
    3072: 'Error calling createmeta JIRA endpoint',
    3082: 'OJ-9084: Changelog history item with no \'fieldId\' or \'field\' key: {keys}',
    2000: ('''ERROR: Mode should be one of "{VALID_RUN_MODES}"'''),
    2101: 'Failed to connect to {git_provider}:\n{e}', # 201
    2102: 'Unable to access project {project_id}, may be a Jira misconfiguration. Skipping...', # 201
    2112: 'Failed to connect to Jira:\n{e}', # 201
    2122: 'you do not have the required \'development field\' permissions in jira required to scan for missing repos', # 201
    2132: (
            'Missing recommended jira_fields! For the best possible experience, '
            'please add the following to `include_fields` in the '
            'configuration file: {missing_required_fields}'
        ),
    2142: (
            'Excluding recommended jira_fields! For the best possible experience, '
            'please remove the following from `exclude_fields` in the '
            'configuration file: {excluded_required_fields}',
        ),
    2201: ('\nERROR: Failed to download ({total_failed}) repo(s) from the group {nrm_project_id}. '
            'Please check that the appropriate permissions are set for the following repos... ({repos_failed_string})'),
    2202: "You do not have the required permissions in jira required to fetch boards for the project {project_id}",
}

def log_and_print(logger, level, msg, exc_info=False):
    '''
    For a failure that should be sent to the logger, and also written
    to stdout (for user visibility)
    '''
    logger.log(level, msg, exc_info=exc_info)
    print(msg, flush=True)
    if exc_info:
        print(traceback.format_exc())


def log_and_print_error_or_warning(logger, level, error_code,msg_args=[], exc_info=False):
    '''
    For a failure that should be sent to the logger, and also written
    to stdout (for user visibility)
    '''
    msg = (f'[{error_code}] {ERROR_MESSAGES.get(error_code).format(*msg_args)}')
    logger.log(level, msg, exc_info=exc_info)
    print(msg, flush=True)
    if exc_info:
        print(traceback.format_exc())
