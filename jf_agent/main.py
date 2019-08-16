import argparse
from datetime import datetime
import gzip
import json
import logging
import os
import pytz
import requests
import subprocess
import sys
from types import GeneratorType
import urllib3

import dateparser
from jira import JIRA
from jira.resources import GreenHopperResource
from stashy.client import Stash
import yaml

from jf_agent.bb_download import (
    get_all_users as get_bb_users,
    get_all_projects as get_bb_projects,
    get_all_repos as get_bb_repos,
    get_default_branch_commits as get_bb_default_branch_commits,
    get_pull_requests as get_bb_pull_requests,
)
from jf_agent.gh_download import (
    get_all_users as get_gh_users,
    get_all_projects as get_gh_projects,
    get_all_repos as get_gh_repos,
    get_default_branch_commits as get_gh_default_branch_commits,
    get_pull_requests as get_gh_pull_requests,
)
from jf_agent.github_client import GithubClient
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
from jf_agent.session import retry_session


def main():
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-c', '--config-file', nargs='?', default='jellyfish.yaml', help='Path to config file'
    )
    parser.add_argument(
        '-s',
        '--since',
        nargs='?',
        default=None,
        help='Pull git activity on or after this timestamp',
    )
    parser.add_argument(
        '-u', '--until', nargs='?', default=None, help='Pull git activity before this timestamp'
    )

    args = parser.parse_args()

    with open(args.config_file, 'r') as ymlfile:
        config = yaml.safe_load(ymlfile)

    conf_global = config.get('global', {})
    skip_ssl_verification = conf_global.get('no_verify_ssl', False)
    compress_output_files = conf_global.get('compress_output_files', True)

    jira_config = config.get('jira', {})
    git_config = config.get('git', config.get('bitbucket', {}))

    jira_url = jira_config.get('url', None)

    git_url = git_config.get('url', None)

    pull_since = dateparser.parse(args.since) if args.since else None
    if pull_since:
        pull_since = pull_since.replace(tzinfo=pytz.utc)

    now = datetime.utcnow()
    pull_until = dateparser.parse(args.until) if args.until else now
    pull_until = pull_until.replace(tzinfo=pytz.utc)

    if not jira_url and not git_url:
        print('ERROR: Config file must provide either a Jira or Bitbucket URL.')
        parser.print_help()
        sys.exit(1)

    if skip_ssl_verification:
        print('WARNING: Disabling SSL certificate validation')
        # To silence "Unverified HTTPS request is being made."
        urllib3.disable_warnings()

    output_basedir = os.environ.get('OUTPUT_BASEDIR', None)
    if not output_basedir:
        print('ERROR: OUTPUT_BASEDIR not found in the environment.')
        return
    outdir = os.path.join(output_basedir, now.strftime('%Y%m%d_%H%M%S'))
    try:
        os.makedirs(outdir, exist_ok=False)
    except FileExistsError:
        print(f"ERROR: Output dir {outdir} already exists")
        return
    except Exception:
        print(
            f"ERROR: Couldn't create output dir {outdir} -- bad OUTPUT_BASEDIR ({output_basedir})?"
        )
        return
    print(f'Will write output files into {outdir}')

    api_token = os.environ.get('JELLYFISH_API_TOKEN')
    if not api_token:
        print(f'ERROR: JELLYFISH_API_TOKEN not found in the environment.')
        return

    resp = requests.get(f'https://jellyfish.co/agent/config?api_token={api_token}')
    if not resp.ok:
        print(f"ERROR: Couldn't get agent config info from https://jellyfish.co/agent/config "
              f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})')
        return
    agent_config = resp.json()

    s3_uri_prefix = agent_config.get('s3_uri_prefix')
    aws_access_key_id = agent_config.get('aws_access_key_id')
    aws_secret_access_key = agent_config.get('aws_secret_access_key')
    if not s3_uri_prefix or not aws_access_key_id or not aws_secret_access_key:
        print(f"ERROR: Missing some required info from the agent config info -- please contact Jellyfish")
        return

    download_data(
        outdir,
        jira_url,
        jira_config,
        skip_ssl_verification,
        git_url,
        git_config,
        pull_since,
        pull_until,
        compress_output_files,
    )

    send_data(outdir, s3_uri_prefix, aws_access_key_id, aws_secret_access_key)

    print('Done!')


def download_data(
    outdir,
    jira_url,
    jira_config,
    skip_ssl_verification,
    git_url,
    git_config,
    pull_since,
    pull_until,
    compress_output_files,
):
    if jira_url:
        jira_username = os.environ.get('JIRA_USERNAME', None)
        jira_password = os.environ.get('JIRA_PASSWORD', None)
        if jira_username and jira_password:
            jira_connection = get_basic_jira_connection(
                jira_url, jira_username, jira_password, skip_ssl_verification
            )
            if jira_connection:
                load_and_dump_jira(outdir, jira_config, jira_connection, compress_output_files)
        else:
            print(
                'ERROR: Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD. Skipping Jira...'
            )

    if git_url:
        provider = git_config.get('provider', 'bitbucket_server')
        if provider not in ('bitbucket_server', 'github'):
            print(
                f'ERROR: unsupported git provider {provider}. Provider should be one of `bitbucket_server` or `github`'
            )
            return

        git_conn = get_git_client(provider, git_url, skip_ssl_verification)
        if git_conn:
            try:
                include_projects = set(git_config.get('include_projects', []))
                exclude_projects = set(git_config.get('exclude_projects', []))
                include_repos = set(git_config.get('include_repos', []))
                exclude_repos = set(git_config.get('exclude_projects', []))
                strip_text_content = git_config.get('strip_text_content', False)
                redact_names_and_urls = git_config.get('redact_names_and_urls', False)

                if provider == 'bitbucket_server':
                    load_and_dump_bb(
                        outdir,
                        git_conn,
                        pull_since,
                        pull_until,
                        include_projects,
                        exclude_projects,
                        include_repos,
                        exclude_repos,
                        strip_text_content,
                        redact_names_and_urls,
                        compress_output_files,
                    )
                elif provider == 'github':
                    load_and_dump_github(
                        outdir,
                        git_conn,
                        pull_since,
                        pull_until,
                        include_projects,
                        exclude_projects,
                        include_repos,
                        exclude_repos,
                        strip_text_content,
                        redact_names_and_urls,
                        compress_output_files,
                    )
                print()
                print(f'Pulled until: {pull_until}')
            except Exception as e:
                print(f'ERROR: Failed to download {provider} data:\n{e}')


def load_and_dump_jira(outdir, jira_config, jira_connection, compress_output_files):
    try:
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

        issue_jql = jira_config.get('issue_jql', '')

        write_file(outdir, 'jira_fields', fields, compress_output_files)

        projects_and_versions = download_projects_and_versions(
            jira_connection,
            include_projects,
            exclude_projects,
            include_categories,
            exclude_categories,
        )
        project_ids = set([proj['id'] for proj in projects_and_versions])
        write_file(
            outdir, 'jira_projects_and_versions', projects_and_versions, compress_output_files
        )

        write_file(
            outdir,
            'jira_users',
            download_users(jira_connection, gdpr_active),
            compress_output_files,
        )
        write_file(
            outdir, 'jira_resolutions', download_resolutions(jira_connection), compress_output_files
        )
        write_file(
            outdir,
            'jira_issuetypes',
            download_issuetypes(jira_connection, project_ids),
            compress_output_files,
        )
        write_file(
            outdir,
            'jira_linktypes',
            download_issuelinktypes(jira_connection),
            compress_output_files,
        )
        write_file(
            outdir, 'jira_priorities', download_priorities(jira_connection), compress_output_files
        )

        boards, sprints, links = download_boards_and_sprints(jira_connection, project_ids)
        write_file(outdir, 'jira_boards', boards, compress_output_files)
        write_file(outdir, 'jira_sprints', sprints, compress_output_files)
        write_file(outdir, 'jira_board_sprint_links', links, compress_output_files)

        issues = download_issues(
            jira_connection, project_ids, include_fields, exclude_fields, issue_jql
        )
        issue_ids = set([i['id'] for i in issues])
        write_file(outdir, 'jira_issues', issues, compress_output_files)

        write_file(
            outdir,
            'jira_worklogs',
            download_worklogs(jira_connection, issue_ids),
            compress_output_files,
        )
    except Exception as e:
        print(f'ERROR: Failed to download jira data:\n{e}')


def get_basic_jira_connection(url, username, password, skip_ssl_verification):
    try:
        return JIRA(
            server=url,
            basic_auth=(username, password),
            max_retries=3,
            options={
                'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
                'verify': not skip_ssl_verification,
            },
        )
    except Exception as e:
        print(f'ERROR: Failed to connect to Jira:\n{e}')


def load_and_dump_github(
    outdir,
    git_conn,
    pull_since,
    pull_until,
    include_projects,
    exclude_projects,
    include_repos,
    exclude_repos,
    strip_text_content,
    redact_names_and_urls,
    compress_output_files,
):
    # github must be in whitelist mode
    if exclude_projects or not include_projects:
        print(
            'ERROR: GitHub Cloud requires a list of projects (i.e., GitHub organizations) to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        return

    users = get_gh_users(git_conn, include_projects)
    write_file(outdir, 'bb_users', users, compress_output_files)

    projects = get_gh_projects(git_conn, include_projects, redact_names_and_urls)
    write_file(outdir, 'bb_projects', projects, compress_output_files)

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_repos, repos = zip(
        *get_gh_repos(
            git_conn, include_projects, include_repos, exclude_repos, redact_names_and_urls
        )
    )
    write_file(outdir, 'bb_repos', repos, compress_output_files)

    commits = get_gh_default_branch_commits(
        git_conn, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
    )
    write_file(outdir, 'bb_commits', commits, compress_output_files)

    prs = get_gh_pull_requests(
        git_conn, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
    )
    write_file(outdir, 'bb_prs', prs, compress_output_files)


def load_and_dump_bb(
    outdir,
    bb_conn,
    pull_since,
    pull_until,
    include_projects,
    exclude_projects,
    include_repos,
    exclude_repos,
    strip_text_content,
    redact_names_and_urls,
    compress_output_files,
):
    users = get_bb_users(bb_conn)
    write_file(outdir, 'bb_users', users, compress_output_files)

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_projects, projects = zip(
        *get_bb_projects(bb_conn, include_projects, exclude_projects, redact_names_and_urls)
    )
    write_file(outdir, 'bb_projects', projects, compress_output_files)

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_repos, repos = zip(
        *get_bb_repos(bb_conn, api_projects, include_repos, exclude_repos, redact_names_and_urls)
    )
    write_file(outdir, 'bb_repos', repos, compress_output_files)

    commits = get_bb_default_branch_commits(
        bb_conn, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
    )
    write_file(outdir, 'bb_commits', commits, compress_output_files)

    prs = get_bb_pull_requests(
        bb_conn, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
    )
    write_file(outdir, 'bb_prs', prs, compress_output_files)


def get_git_client(provider, git_url, skip_ssl_verification):
    if provider == 'bitbucket_server':
        bb_username = os.environ.get('BITBUCKET_USERNAME', None)
        bb_password = os.environ.get('BITBUCKET_PASSWORD', None)
        if not bb_username or not bb_password:
            print(
                'ERROR: Bitbucket credentials not found. Set environment variables BITBUCKET_USERNAME and BITBUCKET_PASSWORD. Skipping Bitbucket...'
            )
            return

        try:
            return Stash(
                base_url=git_url,
                username=bb_username,
                password=bb_password,
                verify=not skip_ssl_verification,
                session=retry_session(),
            )
        except Exception as e:
            print(f'ERROR: Failed to connect to Bitbucket:\n{e}')
            return

    elif provider == 'github':
        gh_token = os.environ.get('GITHUB_TOKEN', None)
        if not gh_token:
            print(
                'ERROR: GitHub credentials not found. Set environment variable GITHUB_TOKEN. Skipping GitHub...'
            )
            return

        try:
            return GithubClient(
                base_url=git_url,
                token=gh_token,
                verify=not skip_ssl_verification,
                session=retry_session(),
            )

        except Exception as e:
            print(f'ERROR: Failed to connect to GitHub:\n{e}')
            return

    raise ValueError(f'unsupported git provider {provider}')


def write_file(outdir, name, results, compress):
    if isinstance(results, GeneratorType):
        results = list(results)

    if compress:
        with gzip.open(f'{outdir}/{name}.json.gz', 'wb') as outfile:
            outfile.write(json.dumps(results, indent=2, default=str).encode('utf-8'))
    else:
        with open(f'{outdir}/{name}.json', 'w') as outfile:
            outfile.write(json.dumps(results, indent=2, default=str))


def send_data(outdir, s3_uri_prefix, aws_access_key_id, aws_secret_access_key):
    def _s3_cmd(cmd):
        try:
            subprocess.check_call(
                f'AWS_ACCESS_KEY_ID={aws_access_key_id} '
                f'AWS_SECRET_ACCESS_KEY={aws_secret_access_key} '
                f'{cmd}', shell=True)
        except Exception:
            print(f'ERROR: aws command failed ({cmd}) -- bad credentials?')
            raise

    print('Sending data to Jellyfish... ')

    done_file_path = f'{outdir}/.done'
    try:
        # A .done file shouldn't yet exist, but we'll make sure
        os.remove(done_file_path)
    except FileNotFoundError:
        pass

    _s3_cmd(f'aws s3 rm {s3_uri_prefix} --recursive')
    _s3_cmd(f'aws s3 sync {outdir} {s3_uri_prefix}')
    os.system(f'touch {done_file_path}')
    _s3_cmd(f'aws s3 sync {done_file_path} {s3_uri_prefix}')


if __name__ == '__main__':
    main()
