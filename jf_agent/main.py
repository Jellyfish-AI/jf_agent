import argparse
import os
import sys
import json
import urllib3
import yaml
import dateparser
import pytz
from datetime import datetime
from stashy.client import Stash

from jira import JIRA
from jira.resources import GreenHopperResource

from jf_agent.bb_download import (
    get_all_users,
    get_all_projects,
    get_all_repos,
    get_default_branch_commits,
    get_pull_requests,
    get_session
)
from jf_agent.jira_download import (
    download_users,
    download_fields,
    download_resolutions,
    download_issuetypes,
    download_issuelinktypes,
    download_priorities,
    download_projects_and_versions,
    download_boards_and_sprints,
    download_issues,
    download_worklogs,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-c', '--config-file', nargs='?', default='jellyfish.yaml', help='Path to config file'
    )
    parser.add_argument(
        '-s',
        '--since',
        nargs='?',
        default=None,
        help='Pull bitbucket activity on or after this timestamp',
    )
    parser.add_argument(
        '-u',
        '--until',
        nargs='?',
        default=None,
        help='Pull bitbucket activity before this timestamp',
    )

    args = parser.parse_args()

    with open(args.config_file, 'r') as ymlfile:
        config = yaml.safe_load(ymlfile)

    conf_global = config.get('global', {})
    outdir = conf_global.get('out_dir', '.')
    skip_ssl_verification = conf_global.get('no_verify_ssl', False)

    jira_config = config.get('jira', {})
    bb_config = config.get('bitbucket', {})

    jira_url = jira_config.get('url', None)
    bb_url = bb_config.get('url', None)

    pull_since = dateparser.parse(args.since) if args.since else None
    if pull_since:
        pull_since = pull_since.replace(tzinfo=pytz.utc)

    pull_until = dateparser.parse(args.until) if args.until else datetime.utcnow()
    pull_until = pull_until.replace(tzinfo=pytz.utc)

    if not jira_url and not bb_url:
        print('ERROR: Config file must provide either a Jira or Bitbucket URL.')
        parser.print_help()
        sys.exit(1)

    if skip_ssl_verification:
        print('WARNING: Disabling SSL certificate validation')
        # To silence "Unverified HTTPS request is being made."
        urllib3.disable_warnings()

    if jira_url:
        jira_username = os.environ.get('JIRA_USERNAME', None)
        jira_password = os.environ.get('JIRA_PASSWORD', None)
        if jira_username and jira_password:
            jira_connection = get_basic_jira_connection(
                jira_url, jira_username, jira_password, skip_ssl_verification
            )
            load_and_dump_jira(outdir, jira_config, jira_connection)
        else:
            print(
                'Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD. Skipping Jira...'
            )

    if bb_url:
        bb_user = os.environ.get('BITBUCKET_USERNAME', None)
        bb_pass = os.environ.get('BITBUCKET_PASSWORD', None)
        if bb_user and bb_pass:
            bb_conn = get_bitbucket_server_client(bb_url, bb_user, bb_pass, skip_ssl_verification)
            load_and_dump_bb(outdir, bb_config, bb_conn, pull_since, pull_until)
            print()
            print(f'Pulled until: {pull_until}')
        else:
            print(
                'Bitbucket credentials not found. Set environment variables BITBUCKET_USERNAME and BITBUCKET_PASSWORD.  Skipping Bitbucket...'
            )


def load_and_dump_jira(outdir, jira_config, jira_connection):
    include_fields = set(jira_config.get('include_fields', []))
    exclude_fields = set(jira_config.get('exclude_fields', []))

    fields = download_fields(jira_connection, include_fields, exclude_fields)

    print_fields_only = jira_config.get('print_fields_only', False)
    if print_fields_only:
        for f in fields:
            print(f"{f['key']:30}\t{f['name']}")
        return

    gdpr_active = jira_config.get('gdpr_active', False)

    include_projects = set(jira_config.get('include_projects', []))
    exclude_projects = set(jira_config.get('exclude_projects', []))
    include_categories = set(jira_config.get('include_project_categories', []))
    exclude_categories = set(jira_config.get('exclude_project_categories', []))

    write_file(outdir, 'jira_fields', fields)

    projects_and_versions = download_projects_and_versions(
        jira_connection, include_projects, exclude_projects, include_categories, exclude_categories
    )
    project_ids = set([proj['id'] for proj in projects_and_versions])
    write_file(outdir, 'jira_projects_and_versions', projects_and_versions)

    write_file(outdir, 'jira_users', download_users(jira_connection, gdpr_active))
    write_file(outdir, 'jira_resolutions', download_resolutions(jira_connection))
    write_file(outdir, 'jira_issuetypes', download_issuetypes(jira_connection, project_ids))
    write_file(outdir, 'jira_linktypes', download_issuelinktypes(jira_connection))
    write_file(outdir, 'jira_priorities', download_priorities(jira_connection))

    boards, sprints, links = download_boards_and_sprints(jira_connection, project_ids)
    write_file(outdir, 'jira_boards', boards)
    write_file(outdir, 'jira_sprints', sprints)
    write_file(outdir, 'jira_board_sprint_links', links)

    issues = download_issues(jira_connection, project_ids, include_fields, exclude_fields)
    issue_ids = set([i['id'] for i in issues])
    write_file(outdir, 'jira_issues', issues)
    write_file(outdir, 'jira_worklogs', download_worklogs(jira_connection, issue_ids))


def get_basic_jira_connection(url, username, password, skip_ssl_verification):
    return JIRA(
        server=url,
        basic_auth=(username, password),
        max_retries=10,
        options={
            'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
            'verify': not skip_ssl_verification,
        },
    )


def load_and_dump_bb(outdir, bb_config, bb_conn, pull_since, pull_until):
    include_projects = set(bb_config.get('include_projects', []))
    exclude_projects = set(bb_config.get('exclude_projects', []))
    include_repos = set(bb_config.get('include_repos', []))
    exclude_repos = set(bb_config.get('exclude_projects', []))
    strip_text_content = bb_config.get('strip_text_content', False)
    redact_names_and_urls = bb_config.get('redact_names_and_urls', False)

    users = get_all_users(bb_conn)
    write_file(outdir, 'bb_users', users)

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_projects, projects = zip(
        *get_all_projects(bb_conn, include_projects, exclude_projects, redact_names_and_urls)
    )
    write_file(outdir, 'bb_projects', projects)

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_repos, repos = zip(
        *get_all_repos(bb_conn, api_projects, include_repos, exclude_repos, redact_names_and_urls)
    )
    write_file(outdir, 'bb_repos', repos)

    commits = get_default_branch_commits(
        bb_conn, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
    )
    write_file(outdir, 'bb_commits', commits)

    prs = get_pull_requests(
        bb_conn, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
    )
    write_file(outdir, 'bb_prs', prs)


def get_bitbucket_server_client(url, username, password, skip_ssl_verification=False):

    with get_session() as session:
        client = Stash(
            base_url=url, username=username, password=password, verify=not skip_ssl_verification, session=session
        )

    return client


def write_file(outdir, name, results):
    with open(f'{outdir}/{name}.json', 'w') as outfile:
        json.dump(list(results), outfile, indent=2, default=str)


if __name__ == '__main__':
    main()
