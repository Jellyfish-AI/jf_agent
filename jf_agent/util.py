import logging
from collections import namedtuple
from itertools import islice
from time import sleep
from typing import Any, List

import requests
from jf_ingest import logging_helper

from jf_agent.exception import BadConfigException
from jf_agent.session import retry_session

logger = logging.getLogger(__name__)


UserProvidedCreds = namedtuple(
    'UserProvidedCreds',
    [
        'jellyfish_api_token',
        'jira_username',
        'jira_password',
        'jira_bearer_token',
        'git_instance_to_creds',
    ],
)

def test_function():
    print('This is a test function, please ignore.')
    # print('This is the same test function, please continue to ignore.')


def get_latest_agent_version():
    try:
        request = requests.get(
            url='https://api.github.com/repos/Jellyfish-AI/jf_agent/commits/master'
        )
        request.raise_for_status()
        return request.json()['sha']
    except Exception as e:
        logging_helper.send_to_agent_log_file(
            f'Exception {e} encountered when trying to get latest Agent version sha'
        )
        return ''


def split(lst: List[Any], n: int) -> List[List[Any]]:
    """
    Split list `lst` into `n` approximately equal chunks
    """
    k, m = divmod(len(lst), n)
    return (lst[i * k + min(i, m) : (i + 1) * k + min(i + 1, m)] for i in range(n))


# a different approach when working with an iterable generator
def batched(iterable, n: int):
    "Batch data into tuples of length n. The last batch may be shorter."
    # batched('ABCDEFG', 3) --> ABC DEF G
    if n < 1:
        raise ValueError('n must be at least one')
    it = iter(iterable)
    while batch := tuple(islice(it, n)):
        yield batch


def get_company_info(config, creds) -> dict:
    base_url = config.jellyfish_api_base
    resp = requests.get(
        f'{base_url}/endpoints/agent/company',
        headers={'Jellyfish-API-Token': creds.jellyfish_api_token},
    )

    if not resp.ok:
        logger.error(
            f"ERROR: Couldn't get company info from {base_url}/agent/company "
            f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})'
        )
        raise BadConfigException()

    company_info = resp.json()

    return company_info


def upload_file(filename, path_to_obj, signed_url, config_outdir, local=False):
    filepath = filename if local else f'{config_outdir}/{filename}'

    total_retries = 5
    retry_count = 0
    while total_retries >= retry_count:
        try:
            with open(filepath, 'rb') as f:
                # If successful, returns HTTP status code 204
                session = retry_session()
                upload_resp = session.post(
                    signed_url['url'],
                    data=signed_url['fields'],
                    files={'file': (path_to_obj, f)},
                )
                upload_resp.raise_for_status()
                logger.info(f'Successfully uploaded {filename}')
                return
        # For large file uploads, we run into intermittent 104 errors where the 'peer' (jellyfish)
        # will appear to shut down the session connection.
        # These exceptions ARE NOT handled by the above retry_session retry logic, which handles 500 level errors.
        # Attempt to catch and retry the 104 type error here
        except requests.exceptions.ConnectionError as e:
            logging_helper.log_standard_error(
                logging.WARNING,
                msg_args=[filename, repr(e)],
                error_code=3001,
                exc_info=True,
            )
            retry_count += 1
            # Back off logic
            sleep(1 * retry_count)

    # If we make it out of the while loop without returning, that means
    # we failed to upload the file.
    logging_helper.log_standard_error(
        logging.ERROR,
        msg_args=[filename],
        error_code=3000,
        exc_info=True,
    )
