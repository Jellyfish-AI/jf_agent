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

# Represents the classification of the logging error/warning/info.
# Provides a way of understanding what groups of people the errors relate to.
ERROR_MESSAGES = {
    000: 'Unclassified',
    '100': 'Success General', # General Customer Success Errors
    '200': 'Customer', # General Customer Errors
    '201': 'Customer Config', # Customer Errors related to their configuration
    '202': 'Customer Permissions', # Customer Error related to their permissions in Jira, Git, etc. or their config.
    '300': 'Engineering', # General Engineering Errors
}

{
    3000: f'Failed to upload file {filename} to S3 bucket',
    3001: f'ERROR: Could not parse response with status code {resp.status_code}. Contact an administrator for help.',
    3100: f'Rate limiter: thought we were operating within our limit (made {calls_made}/{max_calls} calls for {realm}), but got HTTP 429 anyway!',
    3101: f'Next available time to make call is after the timeout of {self.timeout_secs} seconds. Giving up.',
    3200: f'PR {api_pr['id']} doesn't reference a source and/or destination repository; skipping it...',
    3201: f'Error normalizing PR {api_pr["id"]} from repo {repo.id}. Skipping...',
    3202: f'Error getting PRs for repo {repo.id}. Skipping...',
    3203: f'Unable to parse the diff For PR {api_pr["id"]} in repo {repo.id}; proceeding as though no files were changed.',
    3204: f'For PR {api_pr["id"]} in repo {repo.id}, caught HTTPError (HTTP 401) when attempting to retrieve changes; ' f'proceeding as though no files were changed',
    3205: f'For PR {api_pr["id"]} in repo {repo.id}, caught UnicodeDecodeError when attempting to decode changes; proceeding as though no files were changed',
    3300: f'Failed to download {config.git_provider} data:\n{e}',
    3301: f'ValueError: {config.git_provider} is not a supported git_provider for this run_mode',
    3400: f'Got unexpected HTTP 403 for repo {m["url"]}.  Skipping...',
    3401: f'Github rate limit exceeded.  Trying again in {reset_wait_str}...',
    3402: f'Expected an array of json results, but got: {page}',
    3403: f'Got HTTP {e.response.status_code} when fetching commit {ref} for "{full_repo_name}", this likely means you are trying to fetch an invalid ref',
    3500: f'Got {error_name} {response_code} when {action} ({e})',
    3501: f'Got {error_name} {response_code} when {action}',
    3600: f'Failed to download jira data:\n{e}',
    3700: f'Caught KeyError from search_issues(), reducing batch size to {batch_size}',
    3701: 'Caught KeyError from search_issues(), batch size is already 0, bailing out',
    3702: f'Exception encountered in thread {thread_num}\n{traceback.format_exc()}',
    3800: 'Getting HTTP 429s for over an hour; giving up!',
    3900: f'[Thread {thread_num}] Jira issue downloader FAILED',
    3901: f'JIRAError ({e}), reducing batch size to {batch_size}',
    3902: f'Apparently unable to fetch issue based on search_params {search_params}',
    3903: 'Error calling createmeta JIRA endpoint',
    2000: (f'''ERROR: Mode should be one of "{', '.join(VALID_RUN_MODES)}"'''),
    2100: f'Failed to connect to {config.git_provider}:\n{e}', # 201
    2101: f'Unable to access project {project_id}, may be a Jira misconfiguration. Skipping...', # 201
    2102: f'Failed to connect to Jira:\n{e}', # 201
    2103: "you do not have the required 'development field' permissions in jira required to scan for missing repos", # 201
    2104: (
            f'Missing recommended jira_fields! For the best possible experience, '
            f'please add the following to `include_fields` in the '
            f'configuration file: {list(missing_required_fields)}'
        ),
    2105: (
            f'Excluding recommended jira_fields! For the best possible experience, '
            f'please remove the following from `exclude_fields` in the '
            f'configuration file: {list(excluded_required_fields)}',
        ),
    2200: (f'\nERROR: Failed to download ({total_failed}) repo(s) from the group {nrm_project.id}. '
            f'Please check that the appropriate permissions are set for the following repos... ({repos_failed_string})'),
    2201: f"You do not have the required permissions in jira required to fetch boards for the project {project_id}",
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

def log_and_print_error_or_warning(logger, level, msg_args=None, error_code=None, exc_info=False):
    '''
    For a failure that should be sent to the logger, and also written
    to stdout (for user visibility)
    '''
    log_msg = msg
    if level != logging.INFO:  # Don’t care to track codes for info-level logging
        error_message = ERROR_MESSAGES.get(error_code, 'Null Classfication')
        log_msg = f'[{error_code} - {error_message}] {msg}'
    logger.log(level, log_msg, exc_info=exc_info)
    print(msg, flush=True)
    if exc_info:
        print(traceback.format_exc())
