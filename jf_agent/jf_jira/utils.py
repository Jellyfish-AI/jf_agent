import logging
import time
from typing import Optional, Callable, Any

from jf_ingest import logging_helper

logger = logging.getLogger(__name__)


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


def retry_for_status(f: Callable[..., Any], *args, max_retries: int = 5, **kwargs) -> Any:
    """
    This function allows for us to retry 429s from Jira. There are much more elegant ways of accomplishing
    this, but this is a quick and reasonable approach to doing so.

    Note:
        - max_retries=5 will give us a maximum wait time of 10m25s.
    """
    for retry in range(max_retries + 1):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            if hasattr(e, 'status_code') and e.status_code == 429 and retry < max_retries:
                wait_time = get_wait_time(e, retries=retry)
                logging_helper.log_standard_error(
                    logging.WARNING,
                    # NOTE: Getting the function name here isn't always useful,
                    # because sometimes we circumvent the JIRA standard library
                    # and use functions like "get" and "_get_json", but it's still
                    # better than nothing
                    msg_args=[e.status_code, f.__name__, retry, max_retries, wait_time],
                    error_code=3071,
                )
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"Error on attempting to call:\n {getattr(f, '__name__', repr(f))} ")
                logger.error(f"with args:\n {args}\n {kwargs} ")
                raise e
