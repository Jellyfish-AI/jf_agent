import argparse
import os
import sys
import json
import urllib3

from jira import JIRA
from jira.resources import GreenHopperResource
from jf_agent.jira_download import download_users, download_fields, download_resolutions, download_issuetypes, \
    download_issuelinktypes, download_priorities, download_projects_and_versions, download_boards_and_sprints, \
    download_issues, download_worklogs

import stashy
from jf_agent.bb_download import get_all_users, get_all_projects, get_all_repos, get_default_branch_commits, get_pull_requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-o',
        '--out_dir',
        nargs='?',
        default='.',
        help='output directory')
    parser.add_argument(
        '--jira-url',
        type=str,
        help='the fully-qualified base URL for your Jira server, e.g. https://jira.yourcompany.com',
        required=False)
    parser.add_argument(
        '--bitbucket-url',
        type=str,
        help='the fully-qualified base URL for your Bitbucket server, e.g. https://bitbucket.yourcompany.com',
        required=False)
    parser.add_argument(
        '--no-verify-ssl',
        action='store_true',
        help='disable SSL verification when downloading')
    args = parser.parse_args()

    outdir = args.out_dir
    jira_url = args.jira_url
    bb_url = args.bitbucket_url
    skip_ssl_verification = args.no_verify_ssl

    if not jira_url and not bb_url:
        print('ERROR: Please provide either a Jira or Bitbucket URL.')
        parser.print_help()
        sys.exit(1)

    if skip_ssl_verification:
        print('WARNING: Disabling SSL certificate validation')
        # To silence "Unverified HTTPS request is being made."
        urllib3.disable_warnings()

    if jira_url:
        load_and_dump_jira(jira_url, outdir, skip_ssl_verification)

    if bb_url:
        load_and_dump_bb(bb_url, outdir, skip_ssl_verification)


def get_basic_jira_connection(url, username, password, skip_ssl_verification):
    return JIRA(
        server=url,
        basic_auth=(username, password),
        max_retries=10,
        options={
            'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
            'verify': not skip_ssl_verification,
        })


def load_and_dump_jira(jira_url, outdir, skip_ssl_verification=False):
    jira_username = os.environ.get('JIRA_USERNAME', None)
    jira_password = os.environ.get('JIRA_PASSWORD', None)

    if jira_username and jira_password:
        jira_connection = get_basic_jira_connection(
            jira_url, jira_username, jira_password, skip_ssl_verification)
    else:
        print('Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD')
        sys.exit(1)

    write_file(outdir, 'jira_users', download_users(jira_connection))
    write_file(outdir, 'jira_fields', download_fields(jira_connection))
    write_file(outdir, 'jira_resolutions', download_resolutions(jira_connection))
    write_file(outdir, 'jira_issuetypes', download_issuetypes(jira_connection))
    write_file(outdir, 'jira_linktypes', download_issuelinktypes(jira_connection))
    write_file(outdir, 'jira_priorities', download_priorities(jira_connection))
    write_file(outdir, 'jira_projects_and_versions', download_projects_and_versions(jira_connection))

    boards, sprints, links = download_boards_and_sprints(jira_connection)
    write_file(outdir, 'jira_boards', boards)
    write_file(outdir, 'jira_sprints', sprints)
    write_file(outdir, 'jira_board_sprint_links', links)

    write_file(outdir, 'jira_issues', download_issues(jira_connection))
    write_file(outdir, 'jira_worklogs', download_worklogs(jira_connection))


def get_bitbucket_server_client(url, username, password, skip_ssl_verification=False):
    client = stashy.connect(
        url=url,
        username=username,
        password=password,
        verify=not skip_ssl_verification)

    return client


def load_and_dump_bb(bb_url, outdir, skip_ssl_verification):
    bb_user = os.environ.get('BITBUCKET_USERNAME', None)
    bb_pass = os.environ.get('BITBUCKET_PASSWORD', None)

    if bb_user and bb_pass:
        bb_conn = get_bitbucket_server_client(bb_url, bb_user, bb_pass, skip_ssl_verification)
    else:
        print('Bitbucket credendtials not found. Set environment variables BITBUCKET_USERNAME and BITBUCKET_PASSWORD')
        sys.exit(1)

    write_file(outdir, 'bb_users', get_all_users(bb_conn))
    projects = get_all_projects(bb_conn)
    write_file(outdir, 'bb_projects', projects)
    repos = list(get_all_repos(bb_conn, projects))
    write_file(outdir, 'bb_repos', repos)
    write_file(outdir, 'bb_commits', list(get_default_branch_commits(bb_conn, repos)))
    write_file(outdir, 'bb_prs', list(get_pull_requests(bb_conn, repos)))


def write_file(outdir, name, results):
    with open(f'{outdir}/{name}.json', 'w') as outfile:
        json.dump(results, outfile, indent=2, default=str)
