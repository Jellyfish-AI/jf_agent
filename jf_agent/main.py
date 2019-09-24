import argparse
from datetime import datetime
from glob import glob
import gzip
import logging
import os
from pathlib import Path
import requests
import shutil
import subprocess
import traceback
import urllib3

from jira import JIRA
from jira.resources import GreenHopperResource
from stashy.client import Stash
import yaml

from jf_agent import write_file, download_and_write_streaming
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
    download_issue_batch,
    download_worklogs,
)
from jf_agent.session import retry_session

JELLYFISH_API_BASE = 'https://jellyfish.co'


def main():
    logging.basicConfig(level=logging.WARNING)

    valid_run_modes = ('download_and_send', 'download_only', 'send_only', 'print_all_jira_fields')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-m',
        '--mode',
        nargs='?',
        default='download_and_send',
        help=f'Run mode: {", ".join(valid_run_modes)} (default: download_and_send)',
    )
    parser.add_argument(
        '-c',
        '--config-file',
        nargs='?',
        default='./config.yml',
        help='Path to config file (default: ./config.yml)',
    )
    parser.add_argument(
        '-ob',
        '--output-basedir',
        nargs='?',
        default='./output',
        help='Path to output base directory (default: ./output)',
    )
    parser.add_argument(
        '-od',
        '--prev-output-dir',
        nargs='?',
        help='Path to directory containing already-downloaded files',
    )
    parser.add_argument(
        '-s', '--since', nargs='?', default=None, help='DEPRECATED -- has no effect'
    )
    parser.add_argument(
        '-u', '--until', nargs='?', default=None, help='DEPRECATED -- has no effect'
    )

    args = parser.parse_args()

    run_mode = args.mode
    if run_mode not in valid_run_modes:
        print(f'''ERROR: Mode should be one of "{', '.join(valid_run_modes)}"''')
        return

    run_mode_includes_download = run_mode in ('download_and_send', 'download_only')
    run_mode_includes_send = run_mode in ('download_and_send', 'send_only')
    run_mode_is_print_all_jira_fields = run_mode == 'print_all_jira_fields'

    try:
        with open(args.config_file, 'r') as ymlfile:
            config = yaml.safe_load(ymlfile)
    except FileNotFoundError:
        print(f'ERROR: Config file not found at "{args.config_file}"')
        return

    conf_global = config.get('global', {})
    skip_ssl_verification = conf_global.get('no_verify_ssl', False)

    jira_config = config.get('jira', {})
    git_config = config.get('git', config.get('bitbucket', {}))

    jira_url = jira_config.get('url', None)

    git_url = git_config.get('url', None)

    if args.since:
        print(
            f'WARNING: The -s / --since argument is deprecated and has no effect. You can remove its setting.'
        )
    if args.until:
        print(
            f'WARNING: The -u / --until argument is deprecated and has no effect. You can remove its setting.'
        )

    now = datetime.utcnow()

    if not jira_url and not git_url:
        print('ERROR: Config file must provide either a Jira or Git URL.')
        parser.print_help()
        return

    if skip_ssl_verification:
        print('WARNING: Disabling SSL certificate validation')
        # To silence "Unverified HTTPS request is being made."
        urllib3.disable_warnings()

    if run_mode_includes_download:
        if args.prev_output_dir:
            print('ERROR: Provide output_basedir if downloading, not prev_output_dir')
            return

        output_basedir = args.output_basedir
        output_dir_timestamp = now.strftime('%Y%m%d_%H%M%S')
        outdir = os.path.join(output_basedir, output_dir_timestamp)
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

    if run_mode_is_print_all_jira_fields and not jira_url:
        print(f'ERROR: Must provide jira_url for mode {run_mode}')

    if run_mode_includes_send and not run_mode_includes_download:
        if not args.prev_output_dir:
            print('ERROR: prev_output_dir must be provided if not downloading')
            return
        if not os.path.isdir(args.prev_output_dir):
            print(f'ERROR: prev_output_dir ("{args.prev_output_dir}") is not a directory')
            return
        outdir = args.prev_output_dir

    api_token = os.environ.get('JELLYFISH_API_TOKEN')
    if not api_token:
        print(f'ERROR: JELLYFISH_API_TOKEN not found in the environment.')
        return

    resp = requests.get(f'{JELLYFISH_API_BASE}/agent/config?api_token={api_token}')
    if not resp.ok:
        print(
            f"ERROR: Couldn't get agent config info from {JELLYFISH_API_BASE}/agent/config "
            f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})'
        )
        return

    agent_config = resp.json()
    s3_uri_prefix = agent_config.get('s3_uri_prefix')
    aws_access_key_id = agent_config.get('aws_access_key_id')
    aws_secret_access_key = agent_config.get('aws_secret_access_key')
    server_git_instance_info = agent_config.get('git_instance_info')

    if run_mode_includes_send and (
        not s3_uri_prefix or not aws_access_key_id or not aws_secret_access_key
    ):
        print(
            f"ERROR: Missing some required info from the agent config info -- please contact Jellyfish"
        )
        return

    # If we're only downloading, do not compress the output files (so they can be more easily inspected)
    compress_output_files = (
        False if (run_mode_includes_download and not run_mode_includes_send) else True
    )

    jira_connection = None
    if jira_url:
        jira_username = os.environ.get('JIRA_USERNAME', None)
        jira_password = os.environ.get('JIRA_PASSWORD', None)
        if jira_username and jira_password:
            jira_connection = get_basic_jira_connection(
                jira_url, jira_username, jira_password, skip_ssl_verification
            )
        else:
            print(
                'ERROR: Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD. Skipping Jira...'
            )

    if run_mode_is_print_all_jira_fields:
        if jira_connection:
            print_all_jira_fields(jira_connection, jira_config)
        return

    if git_url:
        provider = git_config.get('provider', 'bitbucket_server')
        if provider not in ('bitbucket_server', 'github'):
            print(
                f'ERROR: Unsupported Git provider {provider}. Provider should be one of `bitbucket_server` or `github`'
            )
            return

        git_connection = get_git_client(provider, git_url, skip_ssl_verification)
    else:
        git_connection = None

    if run_mode_includes_download:
        download_data(
            outdir,
            jira_connection,
            jira_config,
            skip_ssl_verification,
            git_connection,
            git_config,
            server_git_instance_info,
            compress_output_files,
        )

    if run_mode_includes_send:
        send_data(outdir, s3_uri_prefix, aws_access_key_id, aws_secret_access_key)
    else:
        print(f'\nSkipping send_data because run_mode is "{run_mode}"')
        print(f'You can now inspect the downloaded data in {outdir}')
        print(f'To send this data to Jellyfish, use "-m send_only -od {outdir}"')

    print('Done!')


def print_all_jira_fields(jira_connection, jira_config):
    include_fields = set(jira_config.get('include_fields', []))
    exclude_fields = set(jira_config.get('exclude_fields', []))
    for f in download_fields(jira_connection, include_fields, exclude_fields):
        print(f"{f['key']:30}\t{f['name']}")


def download_data(
    outdir,
    jira_connection,
    jira_config,
    skip_ssl_verification,
    git_connection,
    git_config,
    server_git_instance_info,
    compress_output_files,
):
    if jira_connection:
        load_and_dump_jira(outdir, jira_config, jira_connection, compress_output_files)

    if git_connection:
        try:
            provider = git_config.get('provider', 'bitbucket_server')
            include_projects = set(git_config.get('include_projects', []))
            exclude_projects = set(git_config.get('exclude_projects', []))
            include_repos = set(git_config.get('include_repos', []))
            exclude_repos = set(git_config.get('exclude_projects', []))
            strip_text_content = git_config.get('strip_text_content', False)
            redact_names_and_urls = git_config.get('redact_names_and_urls', False)

            if provider == 'bitbucket_server':
                load_and_dump_bb(
                    outdir,
                    git_connection,
                    server_git_instance_info,
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
                    git_connection,
                    server_git_instance_info,
                    include_projects,
                    exclude_projects,
                    include_repos,
                    exclude_repos,
                    strip_text_content,
                    redact_names_and_urls,
                    compress_output_files,
                )
        except Exception as e:
            print(f'ERROR: Failed to download {provider} data:\n{e}')
            print(traceback.format_exc())


def load_and_dump_jira(outdir, jira_config, jira_connection, compress_output_files):
    try:
        include_fields = set(jira_config.get('include_fields', []))
        exclude_fields = set(jira_config.get('exclude_fields', []))
        gdpr_active = jira_config.get('gdpr_active', False)
        include_projects = set(jira_config.get('include_projects', []))
        exclude_projects = set(jira_config.get('exclude_projects', []))
        include_categories = set(jira_config.get('include_project_categories', []))
        exclude_categories = set(jira_config.get('exclude_project_categories', []))

        issue_jql = jira_config.get('issue_jql', '')

        write_file(
            outdir,
            'jira_fields',
            compress_output_files,
            download_fields(jira_connection, include_fields, exclude_fields),
        )

        project_ids = None

        def _download_and_write_projects_and_versions():
            projects_and_versions = download_projects_and_versions(
                jira_connection,
                include_projects,
                exclude_projects,
                include_categories,
                exclude_categories,
            )
            nonlocal project_ids
            project_ids = set([proj['id'] for proj in projects_and_versions])
            write_file(
                outdir, 'jira_projects_and_versions', compress_output_files, projects_and_versions
            )

        _download_and_write_projects_and_versions()

        write_file(
            outdir,
            'jira_users',
            compress_output_files,
            download_users(jira_connection, gdpr_active),
        )
        write_file(
            outdir, 'jira_resolutions', compress_output_files, download_resolutions(jira_connection)
        )
        write_file(
            outdir,
            'jira_issuetypes',
            compress_output_files,
            download_issuetypes(jira_connection, project_ids),
        )
        write_file(
            outdir,
            'jira_linktypes',
            compress_output_files,
            download_issuelinktypes(jira_connection),
        )
        write_file(
            outdir, 'jira_priorities', compress_output_files, download_priorities(jira_connection)
        )

        def _download_and_write_boards_and_sprints():
            boards, sprints, links = download_boards_and_sprints(jira_connection, project_ids)
            write_file(outdir, 'jira_boards', compress_output_files, boards)
            write_file(outdir, 'jira_sprints', compress_output_files, sprints)
            write_file(outdir, 'jira_board_sprint_links', compress_output_files, links)

        _download_and_write_boards_and_sprints()

        def _download_and_write_issues():
            return download_and_write_streaming(
                outdir,
                'jira_issues',
                compress_output_files,
                generator_func=download_issue_batch,
                generator_func_args=(
                    jira_connection,
                    project_ids,
                    include_fields,
                    exclude_fields,
                    issue_jql,
                ),
                item_id_dict_key='id',
            )

        issue_ids = _download_and_write_issues()

        write_file(
            outdir,
            'jira_worklogs',
            compress_output_files,
            download_worklogs(jira_connection, issue_ids),
        )
    except Exception as e:
        print(f'ERROR: Failed to download jira data:\n{e}')
        print(traceback.format_exc())


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
        print(traceback.format_exc())


def load_and_dump_github(
    outdir,
    git_conn,
    server_git_instance_info,
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

    write_file(outdir, 'bb_users', compress_output_files, get_gh_users(git_conn, include_projects))

    write_file(
        outdir,
        'bb_projects',
        compress_output_files,
        get_gh_projects(git_conn, include_projects, redact_names_and_urls),
    )

    api_repos = None

    def _get_and_write_repos():
        nonlocal api_repos
        # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
        api_repos, repos = zip(
            *get_gh_repos(
                git_conn, include_projects, include_repos, exclude_repos, redact_names_and_urls
            )
        )
        write_file(outdir, 'bb_repos', compress_output_files, repos)

    _get_and_write_repos()

    def _download_and_write_commits():
        return download_and_write_streaming(
            outdir,
            'bb_commits',
            compress_output_files,
            generator_func=get_gh_default_branch_commits,
            generator_func_args=(
                git_conn,
                api_repos,
                strip_text_content,
                server_git_instance_info,
                redact_names_and_urls,
            ),
            item_id_dict_key='hash',
        )

    _download_and_write_commits()

    def _download_and_write_prs():
        return download_and_write_streaming(
            outdir,
            'bb_prs',
            compress_output_files,
            generator_func=get_gh_pull_requests,
            generator_func_args=(
                git_conn,
                api_repos,
                strip_text_content,
                server_git_instance_info,
                redact_names_and_urls,
            ),
            item_id_dict_key='id',
        )

    _download_and_write_prs()


def load_and_dump_bb(
    outdir,
    bb_conn,
    server_git_instance_info,
    include_projects,
    exclude_projects,
    include_repos,
    exclude_repos,
    strip_text_content,
    redact_names_and_urls,
    compress_output_files,
):
    write_file(outdir, 'bb_users', compress_output_files, get_bb_users(bb_conn))

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_projects, projects = zip(
        *get_bb_projects(bb_conn, include_projects, exclude_projects, redact_names_and_urls)
    )
    write_file(outdir, 'bb_projects', compress_output_files, projects)

    api_repos = None

    def _get_and_write_repos():
        nonlocal api_repos
        # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
        api_repos, repos = zip(
            *get_bb_repos(
                bb_conn, api_projects, include_repos, exclude_repos, redact_names_and_urls
            )
        )
        write_file(outdir, 'bb_repos', compress_output_files, repos)

    _get_and_write_repos()

    def _download_and_write_commits():
        return download_and_write_streaming(
            outdir,
            'bb_commits',
            compress_output_files,
            generator_func=get_bb_default_branch_commits,
            generator_func_args=(
                bb_conn,
                api_repos,
                strip_text_content,
                server_git_instance_info,
                redact_names_and_urls,
            ),
            item_id_dict_key='hash',
        )

    _download_and_write_commits()

    def _download_and_write_prs():
        return download_and_write_streaming(
            outdir,
            'bb_prs',
            compress_output_files,
            generator_func=get_bb_pull_requests,
            generator_func_args=(
                bb_conn,
                api_repos,
                strip_text_content,
                server_git_instance_info,
                redact_names_and_urls,
            ),
            item_id_dict_key='id',
        )

    _download_and_write_prs()


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
            print(traceback.format_exc())
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
            print(traceback.format_exc())
            return

    raise ValueError(f'unsupported git provider {provider}')


def send_data(outdir, s3_uri_prefix, aws_access_key_id, aws_secret_access_key):
    def _s3_cmd(cmd):
        try:
            subprocess.check_call(
                f'AWS_ACCESS_KEY_ID={aws_access_key_id} '
                f'AWS_SECRET_ACCESS_KEY={aws_secret_access_key} '
                f'{cmd}',
                shell=True,
                stdout=subprocess.DEVNULL,
            )
        except Exception:
            print(f'ERROR: aws command failed ({cmd}) -- bad credentials?')
            raise

    # Compress any not yet compressed files before sending
    for fname in glob(f'{outdir}/*.json'):
        print(f'Compressing {fname}')
        with open(fname, 'rb') as f_in:
            with gzip.open(f'{fname}.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(fname)

    print('Sending data to Jellyfish... ')

    output_basedir, output_dir_timestamp = os.path.split(outdir)
    s3_uri_prefix_with_timestamp = os.path.join(s3_uri_prefix, output_dir_timestamp)
    done_file_path = f'{os.path.join(outdir, ".done")}'
    if os.path.exists(done_file_path):
        print(
            f'ERROR: {done_file_path} already exists -- has this data already been sent to Jellyfish?'
        )
        return

    _s3_cmd(f'aws s3 rm {s3_uri_prefix_with_timestamp} --recursive')
    _s3_cmd(f'aws s3 sync {outdir} {s3_uri_prefix_with_timestamp}')
    Path(done_file_path).touch()
    _s3_cmd(f'aws s3 sync {outdir} {s3_uri_prefix_with_timestamp}')


if __name__ == '__main__':
    main()
