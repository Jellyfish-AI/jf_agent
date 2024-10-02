import logging
import traceback
from datetime import datetime
from itertools import chain

from jf_ingest import diagnostics, logging_helper
from jira import JIRA
from jira.resources import GreenHopperResource

from jf_agent.jf_jira.jira_download import download_fields, download_missing_repos_found_by_jira

logger = logging.getLogger(__name__)


def _get_raw_jira_connection(config, creds, max_retries=3):
    kwargs = {
        'server': config.jira_url,
        'max_retries': max_retries,
        'options': {
            'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
            'verify': not config.skip_ssl_verification,
            "headers": {
                "Accept": "application/json;q=1.0, */*;q=0.9",
                'Content-Type': 'application/json',
            },
        },
    }
    if creds.jira_username and creds.jira_password:
        kwargs['basic_auth'] = (creds.jira_username, creds.jira_password)
    elif creds.jira_bearer_token:
        # HACK(asm,2021-10-18): This is copypasta from
        # https://github.com/pycontribs/jira/blob/df8a6a9879b48083ba940ef9b00d6543bcea5015/jira/client.py#L307-L315
        # I would like to get bearer token support merged upstream,
        # however this is a short term fix to enable customers who
        # have already disabled basic authentication.
        kwargs['options']['headers'] = {
            'Authorization': f'Bearer {creds.jira_bearer_token}',
            'Cache-Control': 'no-cache',
            'Content-Type': 'application/json',
            'Accept': "application/json;q=1.0, */*;q=0.9",
            'X-Atlassian-Token': 'no-check',
        }
    else:
        raise RuntimeError(
            'No valid Jira credentials found! Check your JIRA_USERNAME, JIRA_PASSWORD, or JIRA_BEARER_TOKEN environment variables.'
        )

    jira_conn = JIRA(**kwargs)

    jira_conn._session.headers['User-Agent'] = (
        f'jellyfish/1.0 ({jira_conn._session.headers["User-Agent"]})'
    )

    return jira_conn


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def get_basic_jira_connection(config, creds):
    try:
        return _get_raw_jira_connection(config, creds)
    except Exception as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[e], error_code=2102, exc_info=True
        )


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def print_all_jira_fields(config, jira_connection):
    for f in download_fields(
        jira_connection, config.jira_include_fields, config.jira_exclude_fields
    ):
        # This could potential data that clients do not exposed. Print instead of logging here
        print(f"{f['key']:30}\t{f['name']}")


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def print_missing_repos_found_by_jira(config, creds, issues_to_scan):
    missing_repos = download_missing_repos_found_by_jira(config, creds, issues_to_scan)
    # This could potential data that clients do not exposed. Print instead of logging here
    print(
        f'\nScanning the "Development" field on the Jira issues revealed {len(missing_repos)} Git repos apparently missing from Jellyfish'
    )
    for missing_repo in missing_repos:
        print(f"* {missing_repo['name']:30}\t{missing_repo['url']}")
    print('\n')
