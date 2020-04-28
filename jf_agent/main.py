import argparse
import gzip
import logging
import os
import shutil
import threading
from collections import namedtuple
from datetime import datetime, date
from glob import glob
from pathlib import Path

import requests
import urllib3
import yaml

from jf_agent import agent_logging, diagnostics, write_file
from jf_agent.git import load_and_dump_git, get_git_client
from jf_agent.jf_jira import get_basic_jira_connection, print_all_jira_fields, load_and_dump_jira

logger = logging.getLogger(__name__)

JELLYFISH_API_BASE = 'https://jellyfish.co'
VALID_RUN_MODES = ('download_and_send', 'download_only', 'send_only', 'print_all_jira_fields')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-m',
        '--mode',
        nargs='?',
        default='download_and_send',
        help=f'Run mode: {", ".join(VALID_RUN_MODES)} (default: download_and_send)',
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
        '--jellyfish-api-base',
        default=JELLYFISH_API_BASE,
        help=(
            f'For Jellyfish developers: override for JELLYFISH_API_BASE (which defaults to {JELLYFISH_API_BASE}) '
            "-- if you're running the Jellyfish API locally you might use: "
            "http://localhost:8000 (if running the agent container with --network host) or "
            "http://172.17.0.1:8000 (if running the agent container with --network bridge)"
        ),
    )
    parser.add_argument(
        '-s', '--since', nargs='?', default=None, help='DEPRECATED -- has no effect'
    )
    parser.add_argument(
        '-u', '--until', nargs='?', default=None, help='DEPRECATED -- has no effect'
    )

    args = parser.parse_args()
    config = obtain_config(args)
    creds = obtain_creds(config)
    jellyfish_endpoint_info = obtain_jellyfish_endpoint_info(config, creds)
    agent_logging.configure(config.outdir)
    status_json = []

    if config.run_mode == 'send_only':
        # Importantly, don't overwrite the already-existing diagnostics file
        pass

    else:
        print(f'Will write output files into {config.outdir}')
        diagnostics.open_file(config.outdir)

        sys_diag_done_event = threading.Event()
        sys_diag_collector = threading.Thread(
            target=diagnostics.continually_gather_system_diagnostics,
            name='sys_diag_collector',
            args=(sys_diag_done_event, config.outdir),
        )
        sys_diag_collector.start()

        try:
            diagnostics.capture_agent_version()
            diagnostics.capture_run_args(
                args.mode, args.config_file, config.outdir, args.prev_output_dir
            )

            jira_connection = None
            git_connection = None
            if config.jira_url:
                jira_connection = get_basic_jira_connection(config, creds)                    

            if config.run_mode_is_print_all_jira_fields:
                if jira_connection:
                    print_all_jira_fields(config, jira_connection)
                return

            if config.git_url:
                git_connection = get_git_client(config, creds)

            if config.run_mode_includes_download:
                download_data_status = download_data(
                    config,
                    jellyfish_endpoint_info.jira_info,
                    jellyfish_endpoint_info.git_instance_info,
                    jira_connection,
                    git_connection,
                )
                
                write_file(config.outdir, 'status', config.compress_output_files, download_data_status)

            diagnostics.capture_outdir_size(config.outdir)

            # Kills the sys_diag_collector thread
            sys_diag_done_event.set()
            sys_diag_collector.join()

        finally:
            diagnostics.close_file()

    if config.run_mode_includes_send:
        send_data(
            config,
            creds
        )
    else:
        print(f'\nSkipping send_data because run_mode is "{config.run_mode}"')
        print(f'You can now inspect the downloaded data in {config.outdir}')
        print(f'To send this data to Jellyfish, use "-m send_only -od {config.outdir}"')

    print('Done!')


class BadConfigException(Exception):
    pass


ValidatedConfig = namedtuple(
    'ValidatedConfig',
    [
        'run_mode',
        'run_mode_includes_download',
        'run_mode_includes_send',
        'run_mode_is_print_all_jira_fields',
        'skip_ssl_verification',
        'jira_url',
        'jira_earliest_issue_dt',
        'jira_issue_download_concurrent_threads',
        'jira_include_fields',
        'jira_exclude_fields',
        'jira_issue_batch_size',
        'jira_gdpr_active',
        'jira_include_projects',
        'jira_exclude_projects',
        'jira_include_project_categories',
        'jira_exclude_project_categories',
        'jira_issue_jql',
        'jira_download_worklogs',
        'jira_download_sprints',
        'git_provider',
        'git_url',
        'git_include_projects',
        'git_exclude_projects',
        'git_include_bbcloud_projects',
        'git_exclude_bbcloud_projects',
        'git_include_repos',
        'git_exclude_repos',
        'git_strip_text_content',
        'git_redact_names_and_urls',
        'gitlab_per_page_override',
        'outdir',
        'compress_output_files',
        'jellyfish_api_base',
    ],
)

UserProvidedCreds = namedtuple(
    'UserProvidedCreds',
    [
        'jellyfish_api_token',
        'jira_username',
        'jira_password',
        'bb_server_username',
        'bb_server_password',
        'bb_cloud_username',
        'bb_cloud_app_password',
        'github_token',
        'gitlab_token',
    ],
)

JellyfishEndpointInfo = namedtuple(
    'JellyfishEndpointInfo',
    [
        'jira_info',
        'git_instance_info',
    ],
)


def obtain_config(args):
    run_mode = args.mode
    if run_mode not in VALID_RUN_MODES:
        print(f'''ERROR: Mode should be one of "{', '.join(VALID_RUN_MODES)}"''')
        raise BadConfigException()

    run_mode_includes_download = run_mode in ('download_and_send', 'download_only')
    run_mode_includes_send = run_mode in ('download_and_send', 'send_only')
    run_mode_is_print_all_jira_fields = run_mode == 'print_all_jira_fields'
    jellyfish_api_base = args.jellyfish_api_base

    try:
        with open(args.config_file, 'r') as ymlfile:
            yaml_config = yaml.safe_load(ymlfile)
    except FileNotFoundError:
        print(f'ERROR: Config file not found at "{args.config_file}"')
        raise BadConfigException()

    yaml_conf_global = yaml_config.get('global', {})
    skip_ssl_verification = yaml_conf_global.get('no_verify_ssl', False)

    jira_config = yaml_config.get('jira', {})
    jira_url = jira_config.get('url', None)

    jira_earliest_issue_dt = jira_config.get('earliest_issue_dt', None)
    if jira_earliest_issue_dt is not None and type(jira_earliest_issue_dt) != date:
        print(f'ERROR: Invalid format for earliest_issue_dt; should be YYYY-MM-DD')
        raise BadConfigException()

    jira_issue_download_concurrent_threads = jira_config.get(
        'issue_download_concurrent_threads', 10
    )
    jira_include_fields = set(jira_config.get('include_fields', []))
    jira_exclude_fields = set(jira_config.get('exclude_fields', []))
    jira_issue_batch_size = jira_config.get('issue_batch_size', 100)
    jira_gdpr_active = jira_config.get('gdpr_active', False)
    jira_include_projects = set(jira_config.get('include_projects', []))
    jira_exclude_projects = set(jira_config.get('exclude_projects', []))
    jira_include_project_categories = set(jira_config.get('include_project_categories', []))
    jira_exclude_project_categories = set(jira_config.get('exclude_project_categories', []))
    jira_issue_jql = jira_config.get('issue_jql', '')
    jira_download_worklogs = jira_config.get('download_worklogs', True)
    jira_download_sprints = jira_config.get('download_sprints', True)

    if 'bitbucket' in yaml_config:
        # support legacy yaml configuration (where the key _is_ the bitbucket)
        git_config = yaml_config.get('bitbucket', {})
        git_provider = 'bitbucket_server'
    else:
        git_config = yaml_config.get('git', {})
        git_provider = git_config.get('provider')

    git_url = git_config.get('url', None)
    git_include_projects = set(git_config.get('include_projects', []))
    git_exclude_projects = set(git_config.get('exclude_projects', []))
    git_include_bbcloud_projects = set(git_config.get('include_bitbucket_cloud_projects', []))
    git_exclude_bbcloud_projects = set(git_config.get('exclude_bitbucket_cloud_projects', []))
    git_include_repos = set(git_config.get('include_repos', []))
    git_exclude_repos = set(git_config.get('exclude_repos', []))
    git_strip_text_content = git_config.get('strip_text_content', False)
    git_redact_names_and_urls = git_config.get('redact_names_and_urls', False)
    gitlab_per_page_override = git_config.get('gitlab_per_page_override', None)

    if 'git' in yaml_config and not git_provider:
        print(
            f'ERROR: Should add provider for git configuration. Provider should be one of `bitbucket_server`, `github` or `gitlab`'
        )
        raise BadConfigException()

    if git_provider and git_provider not in (
        'bitbucket_server',
        'bitbucket_cloud',
        'github',
        'gitlab',
    ):
        print(
            f'ERROR: Unsupported Git provider {git_provider}. Provider should be one of `bitbucket_server`, `bitbucket_cloud`, `github` or `gitlab`'
        )
        raise BadConfigException()

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
        raise BadConfigException()

    if skip_ssl_verification:
        print('WARNING: Disabling SSL certificate validation')
        # To silence "Unverified HTTPS request is being made."
        urllib3.disable_warnings()

    if run_mode_includes_download:
        if args.prev_output_dir:
            print('ERROR: Provide output_basedir if downloading, not prev_output_dir')
            raise BadConfigException()

    output_basedir = args.output_basedir
    output_dir_timestamp = now.strftime('%Y%m%d_%H%M%S')
    outdir = os.path.join(output_basedir, output_dir_timestamp)
    try:
        os.makedirs(outdir, exist_ok=False)
    except FileExistsError:
        print(f"ERROR: Output dir {outdir} already exists")
        raise BadConfigException()
    except Exception:
        print(
            f"ERROR: Couldn't create output dir {outdir}.  Make sure the output directory you mapped as a docker volume exists on your host."
        )
        raise BadConfigException()

    if run_mode_is_print_all_jira_fields and not jira_url:
        print(f'ERROR: Must provide jira_url for mode {run_mode}')
        raise BadConfigException()

    if run_mode_includes_send and not run_mode_includes_download:
        if not args.prev_output_dir:
            print('ERROR: prev_output_dir must be provided if not downloading')
            raise BadConfigException()

        if not os.path.isdir(args.prev_output_dir):
            print(f'ERROR: prev_output_dir ("{args.prev_output_dir}") is not a directory')
            raise BadConfigException()

        outdir = args.prev_output_dir

    # github must be in whitelist mode
    if git_provider == 'github' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: GitHub requires a list of projects (i.e., GitHub organizations) to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    # gitlab must be in whitelist mode
    if git_provider == 'gitlab' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: GitLab requires a list of projects (i.e., GitLab top-level groups) to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    # BBCloud must be in whitelist mode
    if git_provider == 'bitbucket_cloud' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: Bitbucket Cloud requires a list of projects to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    # If we're only downloading, do not compress the output files (so they can be more easily inspected)
    compress_output_files = (
        False if (run_mode_includes_download and not run_mode_includes_send) else True
    )

    return ValidatedConfig(
        run_mode,
        run_mode_includes_download,
        run_mode_includes_send,
        run_mode_is_print_all_jira_fields,
        skip_ssl_verification,
        jira_url,
        jira_earliest_issue_dt,
        jira_issue_download_concurrent_threads,
        jira_include_fields,
        jira_exclude_fields,
        jira_issue_batch_size,
        jira_gdpr_active,
        jira_include_projects,
        jira_exclude_projects,
        jira_include_project_categories,
        jira_exclude_project_categories,
        jira_issue_jql,
        jira_download_worklogs,
        jira_download_sprints,
        git_provider,
        git_url,
        git_include_projects,
        git_exclude_projects,
        git_include_bbcloud_projects,
        git_exclude_bbcloud_projects,
        git_include_repos,
        git_exclude_repos,
        git_strip_text_content,
        git_redact_names_and_urls,
        gitlab_per_page_override,
        outdir,
        compress_output_files,
        jellyfish_api_base,
    )


def obtain_creds(config):
    jellyfish_api_token = os.environ.get('JELLYFISH_API_TOKEN')
    if not jellyfish_api_token:
        print(f'ERROR: JELLYFISH_API_TOKEN not found in the environment.')
        raise BadConfigException()

    jira_username = os.environ.get('JIRA_USERNAME', None)
    jira_password = os.environ.get('JIRA_PASSWORD', None)
    bb_server_username = os.environ.get('BITBUCKET_USERNAME', None)
    bb_server_password = os.environ.get('BITBUCKET_PASSWORD', None)
    bb_cloud_username = os.environ.get('BITBUCKET_CLOUD_USERNAME', None)
    bb_cloud_app_password = os.environ.get('BITBUCKET_CLOUD_APP_PASSWORD', None)
    github_token = os.environ.get('GITHUB_TOKEN', None)
    gitlab_token = os.environ.get('GITLAB_TOKEN', None)

    if config.jira_url and not (jira_username and jira_password):
        print(
            'ERROR: Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD.'
        )
        raise BadConfigException()

    if config.git_url:
        if config.git_provider == 'bitbucket_server' and not (
            bb_server_username and bb_server_password
        ):
            print(
                'ERROR: Bitbucket credentials not found. Set environment variables BITBUCKET_USERNAME and BITBUCKET_PASSWORD.'
            )
            raise BadConfigException()

        if config.git_provider == 'bitbucket_cloud' and not (
            bb_cloud_username and bb_cloud_app_password
        ):
            print(
                'ERROR: Bitbucket Cloud credentials not found. Set environment variables BITBUCKET_CLOUD_USERNAME and BITBUCKET_CLOUD_APP_PASSWORD.'
            )
            raise BadConfigException()

        if config.git_provider == 'github' and not github_token:
            print('ERROR: GitHub credentials not found. Set environment variable GITHUB_TOKEN.')
            raise BadConfigException()

        if config.git_provider == 'gitlab' and not gitlab_token:
            print('ERROR: GitLab credentials not found. Set environment variable GITLAB_TOKEN.')
            raise BadConfigException()

    return UserProvidedCreds(
        jellyfish_api_token,
        jira_username,
        jira_password,
        bb_server_username,
        bb_server_password,
        bb_cloud_username,
        bb_cloud_app_password,
        github_token,
        gitlab_token,
    )


def obtain_jellyfish_endpoint_info(config, creds):
    base_url = config.jellyfish_api_base
    resp = requests.get(f'{base_url}/endpoints/agent/pull-state', headers={'Jellyfish-API-Token': creds.jellyfish_api_token})

    if not resp.ok:
        print(
            f"ERROR: Couldn't get agent config info from {base_url}/agent/config "
            f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})'
        )
        raise BadConfigException()

    agent_config = resp.json()
    jira_info = agent_config.get('jira_info')
    git_instance_info = agent_config.get('git_instance_info')

    if config.git_url and len(git_instance_info) != 1:
        print(
            f'ERROR: Invalid Git instance info returned from the agent config endpoint -- please contact Jellyfish'
        )
        raise BadConfigException()

    return JellyfishEndpointInfo(
       jira_info, git_instance_info
    )


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def download_data(
    config, endpoint_jira_info, endpoint_git_instance_info, jira_connection, git_connection
):
    download_data_status = []

    if jira_connection:
        download_data_status.append(load_and_dump_jira(config, endpoint_jira_info, jira_connection))
    else:
        download_data_status.append({'type': 'Jira', 'status': 'failed'})

    if git_connection:
        download_data_status.append(
            load_and_dump_git(config, endpoint_git_instance_info, git_connection)
        )
    else: 
        download_data_status.append({'type': 'Git', 'status': 'failed'})

    return download_data_status


def send_data(config, creds):
    _, timestamp = os.path.split(config.outdir)
    def get_signed_url(files):
        base_url = config.jellyfish_api_base
        headers = {'Jellyfish-API-Token': creds.jellyfish_api_token}
        payload = {'files': files}

        r = requests.post(f'{base_url}/endpoints/agent/signed-url?timestamp={timestamp}', headers=headers, json=payload)
        r.raise_for_status()
        
        return r.json()['signed_urls']
            

    def upload_file_from_thread(thread_num, filename, path_to_obj, signed_url):
        try:
            upload_file(filename, path_to_obj, signed_url)
        except Exception as e:
            thread_exceptions[thread_num] = e
            agent_logging.log_and_print(logger,  logging.ERROR, f'Failed to upload file {filename} to S3 bucket', exc_info=True)
    
    def upload_file(filename, path_to_obj, signed_url):
        with open(f'{config.outdir}/{filename}', 'rb') as f:
            # If successful, returns HTTP status code 204
            session = retry_session()
            upload_resp = session.post(signed_url['url'], data=signed_url['fields'], files={'file': (path_to_obj, f)})
            upload_resp.raise_for_status()

    # Compress any not yet compressed files before sending
    for fname in glob(f'{config.outdir}/*.json'):
        print(f'Compressing {fname}')
        with open(fname, 'rb') as f_in:
            with gzip.open(f'{fname}.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(fname)

    print('Sending data to Jellyfish... ')

    signed_urls = get_signed_url(os.listdir(config.outdir))

    threads = [
        threading.Thread(
            target=upload_file,
            args=[index, filename, file_dict['s3_path'], file_dict['url']],
        )
        for index, (filename, file_dict) in enumerate(signed_urls.items())
    ]

    thread_exceptions = [None] * (len(signed_urls))
    
    print(f'Starting {len(threads)} threads')

    for t in thread:
        t.start()

    for t in threads:
        t.join()

    if any(thread_exceptions):
        print(f'ERROR: not all files uploaded to S3. Files have been saved locally. Once connectivity issues are resolved, try running the Agent in send_only mode.')
        return

    # creating .done file
    done_file_path = f'{os.path.join(config.outdir, ".done")}'
    if os.path.exists(done_file_path):
        print(
            f'ERROR: {done_file_path} already exists -- has this data already been sent to Jellyfish?'
        )
        return
    Path(done_file_path).touch()
    done_file_dict = get_signed_url(['.done'])['.done']
    upload_file('.done', done_file_dict['s3_path'], done_file_dict['url'])


if __name__ == '__main__':
    try:
        main()
    except BadConfigException:
        print('ERROR: Bad config; see earlier messages')
