import logging
import time
from typing import Optional, Callable, Any

from jira import JIRAError

from jf_agent import agent_logging
from jf_agent.jf_jira.jira_download import logger


def get_wait_time(e: Optional[Exception], retries: int) -> int:
    """
    This function attempts to standardize determination of a wait time on a retryable failure.
    If the exception's response included a Retry-After header, respect it.
    If it does not, we do an exponential backoff - 5s, 25s, 125s.

    A possible future addition would be to add a jitter factor.
    This is a fairly standard practice but not clearly required for our situation.
    """
    response = getattr(e, 'response', None)
    headers = getattr(response, 'headers', {})
    retry_after = headers.get('Retry-After')
    if retry_after is not None:
        return int(retry_after)
    else:
        return 5 ** retries


def retry_for_429s(f: Callable[..., Any], *args, max_retries: int = 5, **kwargs) -> Any:
    """
    This function allows for us to retry 429s from Jira. There are much more elegant ways of accomplishing
    this, but this is a quick and reasonable approach to doing so.

    Note:
        - max_retries=5 will give us a maximum wait time of 10m25s.
    """
    for retry in range(max_retries + 1):
        try:
            return f(*args, **kwargs)
        except JIRAError as e:
            if hasattr(e, 'status_code') and e.status_code == 429 and retry < max_retries:
                wait_time = get_wait_time(e, retries=retry)
                agent_logging.log_and_print_error_or_warning(
                    logger, logging.WARNING, msg_args=[retry, max_retries, wait_time], error_code=3071,
                )
                time.sleep(wait_time)
                continue
            else:
                raise e
