from typing import Any, List
from itertools import islice
import requests

from jf_agent.exception import BadConfigException

import logging
logger = logging.getLogger(__name__)


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
    while (batch := tuple(islice(it, n))):
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
