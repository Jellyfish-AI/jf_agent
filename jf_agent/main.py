import argparse
import gzip
import logging
import os
import shutil
import threading
from collections import namedtuple
from glob import glob
from pathlib import Path

import requests
import json

from jf_agent import (
    agent_logging,
    diagnostics,
    write_file,
    VALID_RUN_MODES,
    JELLYFISH_API_BASE,
    BadConfigException,
)
from jf_agent.git import load_and_dump_git, get_git_client
from jf_agent.config_file_reader import obtain_config
from jf_agent.jf_jira import (
    get_basic_jira_connection,
    print_all_jira_fields,
    load_and_dump_jira,
    print_missing_repos_found_by_jira,
)
from jf_agent.session import retry_session

logger = logging.getLogger(__name__)


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
        '-ius',
        '--for-print-missing-repos-issues-updated-within-last-x-months',
        type=int,
        choices=range(1, 7),
        help=(
            'scan jira issues that have been updated since the given number of months back (max is 6) '
            'for git repo data, leave blank to only check issues updated in the past month'
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
    agent_logging.configure(config.outdir)

    if config.run_mode == 'validate':
        print('Validating configuration...')
        from jf_jira import _get_raw_jira_connection

        print('Jira details:')
        print(f'  Jira URL:      {config.jira_url}')
        print(f'  Jira Username: {creds.jira_username}')
        pw_len = len(creds.jira_password) - 2
        print(f'  Jira Password: {creds.jira_password[:2]:*<{pw_len}}')
        # test Jira connection
        try:
            print('==> Testing jira connection...')
            jira_connection = _get_raw_jira_connection(config, creds, max_retries=1)
            jira_connection.myself()
        except Exception as e:
            if 'Basic authentication with passwords is deprecated.' in str(e):
                print(
                    f'Error connecting to Jira instance at {config.jira_url}. Please use a Jira API token, see https://confluence.atlassian.com/cloud/api-tokens-938839638.html.'
                )
            else:
                print(
                    f'Error connecting to Jira instance at {config.jira_url}, please validate your credentials. Error: {e}'
                )
            return

        # test jira users permission
        try:
            print('==> Testing jira user browsing permissions...')
            from jf_jira.jira_download import download_users

            user_count = len(download_users(jira_connection, config.jira_gdpr_active, quiet=True))
            print(f'The agent is aware of {user_count} Jira users.')

        except Exception as e:
            print(
                f'Error downloading users from Jira instance at {config.jira_url}, please verify that this user has the "browse all users" permission. Error: {e}'
            )
            return

        # test jira project access
        print('==> Testing jira project permissions...')
        accessible_projects = [p.key for p in jira_connection.projects()]
        print(f'The agent has access to projects {accessible_projects}.')

        if config.jira_include_projects:
            for proj in config.jira_include_projects:
                if proj not in accessible_projects:
                    print(f'Error: Unable to access explicitly-included project {proj}.')
                    return

        print('Success!')

        return

    elif config.run_mode == 'send_only':
        # Importantly, don't overwrite the already-existing diagnostics file
        pass

    else:
        jellyfish_endpoint_info = obtain_jellyfish_endpoint_info(config, creds)

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

            if config.run_mode_is_print_apparently_missing_git_repos:

                issues_to_scan = get_issues_to_scan_from_jellyfish(
                    config, creds, args.for_print_missing_repos_issues_updated_within_last_x_months,
                )
                if issues_to_scan:
                    print_missing_repos_found_by_jira(config, creds, issues_to_scan)
                return

            if config.run_mode_includes_download:
                download_data_status = download_data(
                    config,
                    creds,
                    jellyfish_endpoint_info.jira_info,
                    jellyfish_endpoint_info.git_instance_info,
                )

                write_file(
                    config.outdir, 'status', config.compress_output_files, download_data_status
                )

            diagnostics.capture_outdir_size(config.outdir)

            # Kills the sys_diag_collector thread
            sys_diag_done_event.set()
            sys_diag_collector.join()

        finally:
            diagnostics.close_file()

    if config.run_mode_includes_send:
        send_data(config, creds)
    else:
        print(f'\nSkipping send_data because run_mode is "{config.run_mode}"')
        print(f'You can now inspect the downloaded data in {config.outdir}')
        print(f'To send this data to Jellyfish, use "-m send_only -od {config.outdir}"')

    print('Done!')


UserProvidedCreds = namedtuple(
    'UserProvidedCreds',
    ['jellyfish_api_token', 'jira_username', 'jira_password', 'git_instance_to_creds'],
)

JellyfishEndpointInfo = namedtuple('JellyfishEndpointInfo', ['jira_info', 'git_instance_info'])

required_jira_fields = [
    'issuekey',
    'parent',
    'issuelinks',
    'project',
    'reporter',
    'assignee',
    'creator',
    'issuetype',
    'resolution',
    'resolutiondate',
    'status',
    'created',
    'updated',
    'subtasks',
]


def _get_git_instance_to_creds(git_config):
    def _check_and_get(envvar_name):
        envvar_val = os.environ.get(envvar_name)
        if not envvar_val:
            print(
                f'ERROR: Missing environment variable {envvar_name}. Required for instance {git_config.git_instance_slug}.'
            )
            raise BadConfigException()
        return envvar_val

    git_provider = git_config.git_provider
    prefix = f'{git_config.creds_envvar_prefix}_' if git_config.creds_envvar_prefix else ''
    if git_provider == 'github':
        return {'github_token': _check_and_get(f'{prefix}GITHUB_TOKEN')}
    elif git_provider == 'bitbucket_cloud':
        return {
            'bb_cloud_username': _check_and_get(f'{prefix}BITBUCKET_CLOUD_USERNAME'),
            'bb_cloud_app_password': _check_and_get(f'{prefix}BITBUCKET_CLOUD_APP_PASSWORD'),
        }
    elif git_provider == 'bitbucket_server':
        return {
            'bb_server_username': _check_and_get(f'{prefix}BITBUCKET_USERNAME'),
            'bb_server_password': _check_and_get(f'{prefix}BITBUCKET_PASSWORD'),
        }
    elif git_provider == 'gitlab':
        return {'gitlab_token': _check_and_get(f'{prefix}GITLAB_TOKEN')}


def obtain_creds(config):
    jellyfish_api_token = os.environ.get('JELLYFISH_API_TOKEN')
    if not jellyfish_api_token:
        print('ERROR: JELLYFISH_API_TOKEN not found in the environment.')
        raise BadConfigException()

    jira_username = os.environ.get('JIRA_USERNAME', None)
    jira_password = os.environ.get('JIRA_PASSWORD', None)

    # obtain git slug to credentials
    git_instance_to_creds = {
        git_config.git_instance_slug: _get_git_instance_to_creds(git_config)
        for git_config in config.git_configs
    }

    if config.jira_url and not (jira_username and jira_password):
        print(
            'ERROR: Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD.'
        )
        raise BadConfigException()

    return UserProvidedCreds(
        jellyfish_api_token, jira_username, jira_password, git_instance_to_creds
    )


def obtain_jellyfish_endpoint_info(config, creds):
    base_url = config.jellyfish_api_base
    resp = requests.get(
        f'{base_url}/endpoints/agent/pull-state',
        headers={'Jellyfish-API-Token': creds.jellyfish_api_token},
    )

    if not resp.ok:
        print(
            f"ERROR: Couldn't get agent config info from {base_url}/agent/pull-state "
            f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})'
        )
        raise BadConfigException()

    agent_config = resp.json()
    jira_info = agent_config.get('jira_info')
    git_instance_info = agent_config.get('git_instance_info')

    # if no git info has returned from the endpoint, then an instance may not have been provisioned
    if len(config.git_configs) > 0 and not len(git_instance_info.values()):
        print(
            'ERROR: A Git instance is configured, but no Git instance '
            'info returned from the Jellyfish API -- please contact Jellyfish'
        )
        raise BadConfigException()

    # if there are multiple git configurations
    if len(config.git_configs) > 1:
        for git_config in config.git_configs:
            # assert that each instance in the config is mappable to the instances
            # returned by the endpoint (we'll need this data later)
            slug = git_config.git_instance_slug
            if not git_instance_info.get(slug):
                print(
                    f'ERROR: The Jellyfish API did not return an instance with the git_instance_slug `{slug}` -- '
                    f'please check your configuration or contact Jellyfish'
                )
                raise BadConfigException()

    # If a single Git instance is configured in the YAML, but multiple instances are configured
    # server-side, we don't have a way to map the YAML to the server-side
    if (
        len(config.git_configs) == 1
        and len(git_instance_info.values()) > 1
        and (
            not config.git_configs[0].git_instance_slug
            or not config.git_configs[0].git_instance_slug in git_instance_info
        )
    ):
        print(
            'ERROR: A single Git instance has been configured, but multiple Git instances were returned '
            'from the Jellyfish API -- please contact Jellyfish'
        )
        raise BadConfigException()

    # if multi git instance is configured in the YAML, assert there is a valid git_instance_slug
    if len(config.git_configs) > 1:
        for git_config in config.git_configs:
            if not git_config.git_instance_slug:
                print(
                    'ERROR: Must specify git_instance slug in multi-git mode -- '
                    'please check your configuration or contact Jellyfish'
                )
                raise BadConfigException()

            if git_config.git_instance_slug not in git_instance_info:
                print(
                    f'ERROR: Invalid `instance_slug` {git_config.git_instance_slug} in configuration. -- '
                    'please check your configuration or contact Jellyfish'
                )
                raise BadConfigException()

    return JellyfishEndpointInfo(jira_info, git_instance_info)


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def download_data(config, creds, endpoint_jira_info, endpoint_git_instances_info):
    download_data_status = []

    if config.jira_url:
        agent_logging.log_and_print(
            logger, logging.INFO, 'Obtained Jira configuration, attempting download...',
        )
        jira_connection = get_basic_jira_connection(config, creds)
        if config.run_mode_is_print_all_jira_fields:
            print_all_jira_fields(config, jira_connection)
        download_data_status.append(load_and_dump_jira(config, endpoint_jira_info, jira_connection))

    is_multi_git_config = len(config.git_configs) > 1
    for git_config in config.git_configs:
        agent_logging.log_and_print(
            logger,
            logging.INFO,
            f'Obtained {git_config.git_provider} configuration, attempting download...',
        )
        if is_multi_git_config:
            instance_slug = git_config.git_instance_slug
            instance_info = endpoint_git_instances_info.get(instance_slug)
            instance_creds = creds.git_instance_to_creds.get(instance_slug)
        else:
            # support legacy single-git support, which assumes only one available git instance
            instance_info = list(endpoint_git_instances_info.values())[0]
            instance_creds = list(creds.git_instance_to_creds.values())[0]

        git_connection = get_git_client(
            git_config, instance_creds, skip_ssl_verification=config.skip_ssl_verification
        )

        download_data_status.append(
            load_and_dump_git(
                config=git_config,
                endpoint_git_instance_info=instance_info,
                outdir=config.outdir,
                compress_output_files=config.compress_output_files,
                git_connection=git_connection,
            )
        )

    return download_data_status


def send_data(config, creds):
    _, timestamp = os.path.split(config.outdir)

    def get_signed_url(files):
        base_url = config.jellyfish_api_base
        headers = {'Jellyfish-API-Token': creds.jellyfish_api_token}
        payload = {'files': files}

        r = requests.post(
            f'{base_url}/endpoints/agent/signed-url?timestamp={timestamp}',
            headers=headers,
            json=payload,
        )
        r.raise_for_status()

        return r.json()['signed_urls']

    thread_exceptions = []

    def upload_file_from_thread(filename, path_to_obj, signed_url):
        try:
            upload_file(filename, path_to_obj, signed_url)
        except Exception as e:
            thread_exceptions.append(e)
            agent_logging.log_and_print_error_or_warning(
                logger, logging.ERROR, msg_args=[filename], error_code=3000, exc_info=True,
            )

    def upload_file(filename, path_to_obj, signed_url, local=False):
        filepath = filename if local else f'{config.outdir}/{filename}'

        with open(filepath, 'rb') as f:
            # If successful, returns HTTP status code 204
            session = retry_session()
            upload_resp = session.post(
                signed_url['url'], data=signed_url['fields'], files={'file': (path_to_obj, f)}
            )
            upload_resp.raise_for_status()

    # Compress any not yet compressed files before sending
    for fname in glob(f'{config.outdir}/*.json'):
        print(f'Compressing {fname}')
        with open(fname, 'rb') as f_in:
            with gzip.open(f'{fname}.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(fname)

    print('Sending data to Jellyfish... ')

    # obtain file names from the directory
    _, directories, filenames = next(os.walk(config.outdir))

    # get the full file paths for each of the immediate
    # subdirectories (we're assuming only a single level)
    for directory in directories:
        path = os.path.join(config.outdir, directory)
        for file_name in os.listdir(path):
            filenames.append(f'{directory}/{file_name}')

    signed_urls = get_signed_url(filenames)

    threads = [
        threading.Thread(
            target=upload_file_from_thread, args=[filename, file_dict['s3_path'], file_dict['url']],
        )
        for (filename, file_dict) in signed_urls.items()
    ]

    print(f'Starting {len(threads)} threads')

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    if any(thread_exceptions):
        print(
            'ERROR: not all files uploaded to S3. Files have been saved locally. Once connectivity issues are resolved, try running the Agent in send_only mode.'
        )
        return

    # If sending agent config flag is on, upload config.yml to s3 bucket
    if config.send_agent_config:
        config_file_dict = get_signed_url(['config.yml'])['config.yml']
        upload_file('config.yml', config_file_dict['s3_path'], config_file_dict['url'], local=True)

    # creating .done file
    done_file_path = f'{os.path.join(config.outdir, ".done")}'
    Path(done_file_path).touch()
    done_file_dict = get_signed_url(['.done'])['.done']
    upload_file('.done', done_file_dict['s3_path'], done_file_dict['url'])


def get_issues_to_scan_from_jellyfish(config, creds, updated_within_last_x_months):
    base_url = config.jellyfish_api_base
    api_token = creds.jellyfish_api_token

    params = {}
    if updated_within_last_x_months:
        params.update({'monthsback': updated_within_last_x_months})

    print('Fetching Jira issues that are missing Git repo data in Jellyfish...')

    resp = requests.get(
        f'{base_url}/endpoints/agent/unlinked-dev-issues',
        headers={'Jellyfish-API-Token': api_token},
        params=params,
    )

    # try and grab any specific error messages sent over
    try:
        data = resp.json()
        print(data.get('message', ''))
    except json.decoder.JSONDecodeError:
        print(
            f'ERROR: Could not parse response with status code {resp.status_code}. Contact an administrator for help.'
        )
        return None

    if resp.status_code == 400:
        # additionally, indicate config needs alterations
        raise BadConfigException()
    elif not resp.ok:
        return None

    return data.get('issues')


if __name__ == '__main__':
    try:
        main()
    except BadConfigException:
        print('ERROR: Bad config; see earlier messages')
