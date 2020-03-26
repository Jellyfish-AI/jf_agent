import argparse
import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from collections import namedtuple
from datetime import datetime
from glob import glob
from pathlib import Path

import requests
import urllib3
import yaml
from jira import JIRA
from jira.resources import GreenHopperResource
from jf_agent.git import load_and_dump_git, get_git_client

from jf_agent import agent_logging, diagnostics, download_and_write_streaming, write_file
from jf_agent.jira_download import (
    download_boards_and_sprints,
    download_customfieldoptions,
    download_fields,
    download_issue_batch,
    download_issuelinktypes,
    download_issuetypes,
    download_priorities,
    download_projects_and_versions,
    download_resolutions,
    download_users,
    download_worklogs,
)

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
        '-debug', '--debug', action="store_true", help='Debug mode (for Jellyfish developers only)'
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
            diagnostics.capture_run_args(
                args.mode, args.config_file, config.outdir, args.prev_output_dir
            )

            jira_connection = None
            if config.jira_url:
                jira_connection = get_basic_jira_connection(config, creds)
                if jira_connection is None:
                    status_json.append({'type': 'Jira', 'status': 'failed'})

            if config.run_mode_is_print_all_jira_fields:
                print_all_jira_fields(config, jira_connection)
                return

            if config.git_url:
                git_connection = get_git_client(config, creds)
                if git_connection is None:
                    status_json.append({'type': 'Git', 'status': 'failed'})
            else:
                git_connection = None

            if config.run_mode_includes_download:
                download_data_status = download_data(
                    config,
                    jellyfish_endpoint_info.git_instance_info,
                    jira_connection,
                    git_connection,
                )

                for item in download_data_status:
                    status_json.append(item)

            diagnostics.capture_outdir_size(config.outdir)

            # Kills the sys_diag_collector thread
            sys_diag_done_event.set()
            sys_diag_collector.join()

        finally:
            write_file(config.outdir, 'status', config.compress_output_files, status_json)
            diagnostics.close_file()

    if config.run_mode_includes_send:
        send_data(
            config,
            creds,
            config.outdir,
            jellyfish_endpoint_info.s3_uri_prefix,
            
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
        'jira_include_fields',
        'jira_exclude_fields',
        'jira_gdpr_active',
        'jira_include_projects',
        'jira_exclude_projects',
        'jira_include_project_categories',
        'jira_exclude_project_categories',
        'jira_issue_jql',
        'git_provider',
        'git_url',
        'git_include_projects',
        'git_exclude_projects',
        'git_include_repos',
        'git_exclude_repos',
        'git_strip_text_content',
        'git_redact_names_and_urls',
        'gitlab_per_page_override',
        'outdir',
        'compress_output_files',
        'debug',
        'debug_base_url',
    ],
)

UserProvidedCreds = namedtuple(
    'UserProvidedCreds',
    [
        'jellyfish_api_token',
        'jira_username',
        'jira_password',
        'bb_username',
        'bb_password',
        'github_token',
        'gitlab_token',
    ],
)

JellyfishEndpointInfo = namedtuple(
    'JellyfishEndpointInfo',
    ['s3_uri_prefix', 'git_instance_info'],
)


def obtain_config(args):
    run_mode = args.mode
    if run_mode not in VALID_RUN_MODES:
        print(f'''ERROR: Mode should be one of "{', '.join(VALID_RUN_MODES)}"''')
        raise BadConfigException()

    run_mode_includes_download = run_mode in ('download_and_send', 'download_only')
    run_mode_includes_send = run_mode in ('download_and_send', 'send_only')
    run_mode_is_print_all_jira_fields = run_mode == 'print_all_jira_fields'
    debug = args.debug

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
    jira_include_fields = set(jira_config.get('include_fields', []))
    jira_exclude_fields = set(jira_config.get('exclude_fields', []))
    jira_gdpr_active = jira_config.get('gdpr_active', False)
    jira_include_projects = set(jira_config.get('include_projects', []))
    jira_exclude_projects = set(jira_config.get('exclude_projects', []))
    jira_include_project_categories = set(jira_config.get('include_project_categories', []))
    jira_exclude_project_categories = set(jira_config.get('exclude_project_categories', []))
    jira_issue_jql = jira_config.get('issue_jql', '')

    git_config = yaml_config.get('git', yaml_config.get('bitbucket', {}))
    git_provider = git_config.get('provider', 'bitbucket_server')
    git_url = git_config.get('url', None)
    git_include_projects = set(git_config.get('include_projects', []))
    git_exclude_projects = set(git_config.get('exclude_projects', []))
    git_include_repos = set(git_config.get('include_repos', []))
    git_exclude_repos = set(git_config.get('exclude_repos', []))
    git_strip_text_content = git_config.get('strip_text_content', False)
    git_redact_names_and_urls = git_config.get('redact_names_and_urls', False)
    gitlab_per_page_override = git_config.get('gitlab_per_page_override', None)

    debug_config = yaml_config.get('debug', {})
    debug_base_url = debug_config.get('base_url', None)

    if debug and not debug_base_url:
        print(f'ERROR: Should provide debug_base_url for debug mode')
        raise BadConfigException()

    if git_provider not in ('bitbucket_server', 'github', 'gitlab'):
        print(
            f'ERROR: Unsupported Git provider {git_provider}. Provider should be one of `bitbucket_server`, `github` or `gitlab`'
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
            'ERROR: GitHub Cloud requires a list of projects (i.e., GitHub organizations) to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    # gitlab must be in whitelist mode
    if git_provider == 'gitlab' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: GitLab requires a list of projects (i.e., GitLab top-level groups) to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
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
        jira_include_fields,
        jira_exclude_fields,
        jira_gdpr_active,
        jira_include_projects,
        jira_exclude_projects,
        jira_include_project_categories,
        jira_exclude_project_categories,
        jira_issue_jql,
        git_provider,
        git_url,
        git_include_projects,
        git_exclude_projects,
        git_include_repos,
        git_exclude_repos,
        git_strip_text_content,
        git_redact_names_and_urls,
        gitlab_per_page_override,
        outdir,
        compress_output_files,
        debug,
        debug_base_url,
    )


def obtain_creds(config):
    jellyfish_api_token = os.environ.get('JELLYFISH_API_TOKEN')
    if not jellyfish_api_token:
        print(f'ERROR: JELLYFISH_API_TOKEN not found in the environment.')
        raise BadConfigException()

    jira_username = os.environ.get('JIRA_USERNAME', None)
    jira_password = os.environ.get('JIRA_PASSWORD', None)
    bb_username = os.environ.get('BITBUCKET_USERNAME', None)
    bb_password = os.environ.get('BITBUCKET_PASSWORD', None)
    github_token = os.environ.get('GITHUB_TOKEN', None)
    gitlab_token = os.environ.get('GITLAB_TOKEN', None)

    if config.jira_url and not (jira_username and jira_password):
        print(
            'ERROR: Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD.'
        )
        raise BadConfigException()

    if config.git_url:
        if config.git_provider == 'bitbucket_server' and not (bb_username and bb_password):
            print(
                'ERROR: Bitbucket credentials not found. Set environment variables BITBUCKET_USERNAME and BITBUCKET_PASSWORD.'
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
        bb_username,
        bb_password,
        github_token,
        gitlab_token,
    )


def obtain_jellyfish_endpoint_info(config, creds):

    base_url = config.debug_base_url if config.debug else JELLYFISH_API_BASE
    resp = requests.get(f'{base_url}/agent/config?api_token={creds.jellyfish_api_token}')

    if not resp.ok:
        print(
            f"ERROR: Couldn't get agent config info from {base_url}/agent/config "
            f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})'
        )
        raise BadConfigException()

    agent_config = resp.json()
    s3_uri_prefix = agent_config.get('s3_uri_prefix')
    git_instance_info = agent_config.get('git_instance_info')

    # Validate the S3 prefix, bail out if invalid value is found.
    if not re.fullmatch(r'^s3:\/\/[A-Za-z0-9:\/\-_]+\/[A-Za-z0-9:\/\-_]+$', s3_uri_prefix or ''):
        print(
            "ERROR: The S3 bucket information provided by the agent config endpoint is invalid "
            "-- please contact Jellyfish"
        )
        raise BadConfigException()

    if config.run_mode_includes_send and (
        not s3_uri_prefix 
    ):
        print(
            f"ERROR: Missing some required info from the agent config info -- please contact Jellyfish"
        )
        raise BadConfigException()

    if config.git_url and len(git_instance_info) != 1:
        print(
            f'ERROR: Invalid Git instance info returned from the agent config endpoint -- please contact Jellyfish'
        )
        raise BadConfigException()

    return JellyfishEndpointInfo(
        s3_uri_prefix, git_instance_info
    )


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def print_all_jira_fields(config, jira_connection):
    for f in download_fields(
        jira_connection, config.jira_include_fields, config.jira_exclude_fields
    ):
        print(f"{f['key']:30}\t{f['name']}")


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def download_data(config, endpoint_git_instance_info, jira_connection, git_connection):
    download_data_status = []

    if jira_connection:
        download_data_status.append(load_and_dump_jira(config, jira_connection))

    if git_connection:
        download_data_status.append(
            load_and_dump_git(config, endpoint_git_instance_info, git_connection)
        )

    return download_data_status


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def load_and_dump_jira(config, jira_connection):
    try:
        write_file(
            config.outdir,
            'jira_fields',
            config.compress_output_files,
            download_fields(
                jira_connection, config.jira_include_fields, config.jira_exclude_fields
            ),
        )

        projects_and_versions = download_projects_and_versions(
            jira_connection,
            config.jira_include_projects,
            config.jira_exclude_projects,
            config.jira_include_project_categories,
            config.jira_exclude_project_categories,
        )

        project_ids = {proj['id'] for proj in projects_and_versions}
        write_file(
            config.outdir,
            'jira_projects_and_versions',
            config.compress_output_files,
            projects_and_versions,
        )

        write_file(
            config.outdir,
            'jira_users',
            config.compress_output_files,
            download_users(jira_connection, config.jira_gdpr_active),
        )
        write_file(
            config.outdir,
            'jira_resolutions',
            config.compress_output_files,
            download_resolutions(jira_connection),
        )
        write_file(
            config.outdir,
            'jira_issuetypes',
            config.compress_output_files,
            download_issuetypes(jira_connection, project_ids),
        )
        write_file(
            config.outdir,
            'jira_linktypes',
            config.compress_output_files,
            download_issuelinktypes(jira_connection),
        )
        write_file(
            config.outdir,
            'jira_priorities',
            config.compress_output_files,
            download_priorities(jira_connection),
        )

        def download_and_write_boards_and_sprints():
            boards, sprints, links = download_boards_and_sprints(jira_connection, project_ids)
            write_file(config.outdir, 'jira_boards', config.compress_output_files, boards)
            write_file(config.outdir, 'jira_sprints', config.compress_output_files, sprints)
            write_file(
                config.outdir, 'jira_board_sprint_links', config.compress_output_files, links
            )

        download_and_write_boards_and_sprints()

        @diagnostics.capture_timing()
        @agent_logging.log_entry_exit(logger)
        def download_and_write_issues():
            return download_and_write_streaming(
                config.outdir,
                'jira_issues',
                config.compress_output_files,
                generator_func=download_issue_batch,
                generator_func_args=(
                    jira_connection,
                    project_ids,
                    config.jira_include_fields,
                    config.jira_exclude_fields,
                    config.jira_issue_jql,
                ),
                item_id_dict_key='id',
            )

        issue_ids = download_and_write_issues()

        write_file(
            config.outdir,
            'jira_worklogs',
            config.compress_output_files,
            download_worklogs(jira_connection, issue_ids),
        )
        write_file(
            config.outdir,
            'jira_customfieldoptions',
            config.compress_output_files,
            download_customfieldoptions(jira_connection),
        )

        return {'type': 'Jira', 'status': 'success'}
    except Exception as e:
        agent_logging.log_and_print(
            logger, logging.ERROR, f'Failed to download jira data:\n{e}', exc_info=True
        )

        return {'type': 'Jira', 'status': 'failed'}


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def get_basic_jira_connection(config, creds):
    try:
        return JIRA(
            server=config.jira_url,
            basic_auth=(creds.jira_username, creds.jira_password),
            max_retries=3,
            options={
                'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
                'verify': not config.skip_ssl_verification,
            },
        )
    except Exception as e:
        agent_logging.log_and_print(
            logger, logging.ERROR, f'Failed to connect to Jira:\n{e}', exc_info=True
        )

def send_data(config, creds, outdir, s3_uri_prefix):
    # Compress any not yet compressed files before sending
    for fname in glob(f'{outdir}/*.json'):
        print(f'Compressing {fname}')
        with open(fname, 'rb') as f_in:
            with gzip.open(f'{fname}.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(fname)

    print('Sending data to Jellyfish... ')

    output_basedir, output_dir_timestamp = os.path.split(outdir)
    s3_uri_prefix_with_timestamp = os.path.join(s3_uri_prefix, output_dir_timestamp) # jellyfish_agent/company/ + timestamp/
    done_file_path = f'{os.path.join(outdir, ".done")}'
    if os.path.exists(done_file_path):
        print(
            f'ERROR: {done_file_path} already exists -- has this data already been sent to Jellyfish?'
        )
        return

    base_url = config.debug_base_url if config.debug else JELLYFISH_API_BASE

    bucket_object_path = s3_uri_prefix_with_timestamp[5: len(s3_uri_prefix_with_timestamp)].split('/', 1)
    headers = {'Jellyfish-API-Token': creds.jellyfish_api_token}

    for filename in os.listdir(outdir):
        payload = {'bucket': bucket_object_path[0], 'object': f'{bucket_object_path[1]}/' + filename}
        r = requests.post(f'{base_url}/endpoints/create-signed-url', headers=headers, json=payload).json()
        signed_url = r["signedUrl"]
        path_to_obj = r['objectPath']

        with open(f'{outdir}/'+ filename, 'rb') as f:
            # If successful, returns HTTP status code 204
            upload_resp = requests.post(signed_url['url'], data=signed_url['fields'], files={'file': (path_to_obj, f)})
            logger.info(f'File upload HTTP status code: {upload_resp.status_code}')


if __name__ == '__main__':
    try:
        main()
    except BadConfigException:
        print('ERROR: Bad config; see earlier messages')
