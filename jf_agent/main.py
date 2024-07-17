import argparse
from concurrent.futures import ThreadPoolExecutor
import traceback
import dotenv
import gzip
import logging
import os
import shutil
import threading
from collections import namedtuple
from glob import glob
from pathlib import Path
import sys

import requests
import json

from jf_agent import (
    agent_logging,
    write_file,
    VALID_RUN_MODES,
    JELLYFISH_API_BASE,
    JELLYFISH_WEBHOOK_BASE,
)
from jf_agent.exception import BadConfigException
from jf_agent.data_manifests.jira.generator import create_manifest as create_jira_manifest
from jf_agent.data_manifests.git.generator import create_manifests as create_git_manifests
from jf_agent.data_manifests.manifest import Manifest
from jf_agent.git import load_and_dump_git, get_git_client
from jf_agent.config_file_reader import get_ingest_config, obtain_config
from jf_agent.jf_jira import (
    get_basic_jira_connection,
    print_all_jira_fields,
    load_and_dump_jira,
    print_missing_repos_found_by_jira,
)

from jf_agent.validation import (
    full_validate,
    validate_num_repos,
    ProjectMetadata,
)

from jf_agent.util import get_company_info, upload_file

from jf_ingest import diagnostics, logging_helper
from jf_ingest.jf_jira import load_and_push_jira_to_s3
from jf_ingest.config import IngestionType, IngestionConfig

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
        '--jellyfish-webhook-base',
        default=JELLYFISH_WEBHOOK_BASE,
        help=(
            f'For Jellyfish developers: override for JELLYFISH_WEBHOOK_BASE (which defaults to {JELLYFISH_WEBHOOK_BASE}) '
            "-- if you're running the Jellyfish webhook service locally you might use: "
            "http://localhost:4999 (if running the agent container with --network host) or "
            "http://172.17.0.1:4999 (if running the agent container with --network bridge)"
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
        '-e',
        '--env-file',
        type=str,
        help='File path to a .env credentials file. Useful for running the agent in a local developer context',
    )
    parser.add_argument(
        '-db',
        '--debug-requests',
        action='store_true',
        help='Enable http requests debug logging. WARNING, this is VERY verbose and WILL print out all headers '
        'and bodies of all requests made by the agent, INCLUDING bearer tokens. Use only to debug errors.',
    )
    parser.add_argument(
        '-s', '--since', nargs='?', default=None, help='DEPRECATED -- has no effect'
    )
    parser.add_argument(
        '-u', '--until', nargs='?', default=None, help='DEPRECATED -- has no effect'
    )
    parser.add_argument(
        '-f',
        '--from-failure',
        action='store_true',
        help='To be run with -m send-only. Indicates that we have had a failure '
        'but we will try to send the data anyways, but will *not* try to redownload.',
    )

    args = parser.parse_args()

    config = obtain_config(args)

    if args.env_file:
        dotenv.load_dotenv(args.env_file)

    creds = obtain_creds(config)
    logging_config = agent_logging.configure(
        config.outdir,
        config.jellyfish_webhook_base,
        creds.jellyfish_api_token,
        config.debug_http_requests,
    )

    success = True
    jellyfish_endpoint_info = obtain_jellyfish_endpoint_info(config, creds)

    # send start signal to Agent heartbeat monitor
    try:
        diagnostics.send_diagnostic_start_reading(
            jellyfish_webhook_base=JELLYFISH_WEBHOOK_BASE,
            jellyfish_api_token=creds.jellyfish_api_token,
            timestamp_key=get_timestamp_from_outdir(config.outdir),
            will_run_git=bool(config.git_configs),
            will_run_jira=bool(config.jira_url),
            will_send=not config.run_mode == 'send_only',
        )
    except Exception as e:
        logging_helper.send_to_agent_log_file(
            f'Error encountered when attempting to send diagnostic heart beat start (Error: {e})',
            level=logging.ERROR,
        )

    logger.info(f'Will write output files into {config.outdir}')
    diagnostics.open_file(config.outdir)

    sys_diag_done_event = threading.Event()
    sys_diag_collector = threading.Thread(
        target=diagnostics.continually_gather_system_diagnostics,
        name='sys_diag_collector',
        args=(
            sys_diag_done_event,
            config.outdir,
            JELLYFISH_WEBHOOK_BASE,
            creds.jellyfish_api_token,
        ),
    )
    try:
        sys_diag_collector.start()

        # This will only get set if --from-failure is passed down, indicating that the run is being re-run from failure
        # to try to upload failed data.
        error_and_timeout_free = True

        if config.run_mode == 'validate':
            try:
                full_validate(
                    config,
                    creds,
                    jellyfish_endpoint_info,
                    upload=not config.skip_healthcheck_upload,
                )
            except Exception as err:
                logger.error(
                    f"Failed to run healthcheck validation due to exception, moving on. Exception: {err}"
                )

        elif args.from_failure:
            error_and_timeout_free = False
            old_files = os.listdir(args.output_basedir)
            files = sorted(old_files, reverse=True)

            # This needs to be the second on the list
            # Since we'll have already created a first one for * this * run
            # And the timestamps won't line up
            if len(files) < 2:
                logger.error('No previous directory to mount, cannot send data post-failure!')
                return False

            previous_run_file = files[1]

            config = config._replace(outdir=os.path.join(args.output_basedir, previous_run_file))

            logger.info(f"Mounted old output directory as {config.outdir}, will attempt to send.")

        elif not config.run_mode == 'send_only':
            # Importantly, don't overwrite the already-existing diagnostics file
            try:
                # Run Jira validation from JF ingest by default.
                # Temporarily skip Git until we cut the validation over to JF ingest
                logger.info("Running ingestion healthcheck validation!")
                full_validate(config, creds, jellyfish_endpoint_info, skip_git=True)
            except Exception as err:
                logger.error(
                    f"Failed to run healthcheck validation due to exception, moving on. Exception: {err}"
                )

            try:
                if jellyfish_endpoint_info.jf_options.get('validate_num_repos', False):
                    validate_num_repos(config.git_configs, creds)
            except Exception as e:
                logger.warning(f"Could not validate client/org creds, moving on. Got {e}")

            try:
                diagnostics.capture_agent_version()
                diagnostics.capture_run_args(
                    args.mode, args.config_file, config.outdir, args.prev_output_dir
                )

                if config.run_mode_is_print_apparently_missing_git_repos:
                    issues_to_scan = get_issues_to_scan_from_jellyfish(
                        config,
                        creds,
                        args.for_print_missing_repos_issues_updated_within_last_x_months,
                    )
                    if issues_to_scan:
                        print_missing_repos_found_by_jira(config, creds, issues_to_scan)
                    return True

                if config.run_mode_includes_download:
                    download_data_status = download_data(
                        config,
                        creds,
                        jellyfish_endpoint_info.jira_info,
                        jellyfish_endpoint_info.git_instance_info,
                        jellyfish_endpoint_info.jf_options,
                    )
                    success = all(s['status'] == 'success' for s in download_data_status)
                    write_file(
                        config.outdir, 'status', config.compress_output_files, download_data_status
                    )

                diagnostics.capture_outdir_size(config.outdir)

            except Exception as err:
                logger.error(f"Encountered error during agent run! {err}")
                agent_logging.close_out(logging_config)
                return False

    finally:

        # Kills the sys_diag_collector thread.
        # We need to do this before exiting, otherwise we'll hang forever and never exit until timeout kills it.
        logger.info('Shutting down Systems Diagnostics Thread')
        sys_diag_done_event.set()
        sys_diag_collector.join()

        logger.info('Closing Diagnostics file')
        # We need to close this before we send data
        # Otherwise we'll send a .fuse_hidden file (temp file)
        diagnostics.close_file()

    success &= potentially_send_data(config, creds, successful=error_and_timeout_free)

    logger.info('Done!')

    try:
        diagnostics.send_diagnostic_end_reading(
            jellyfish_webhook_base=JELLYFISH_WEBHOOK_BASE,
            jellyfish_api_token=creds.jellyfish_api_token,
            timestamp_key=get_timestamp_from_outdir(config.outdir),
            git_success=None,
            jira_success=None,
        )
    except Exception as e:
        logging_helper.send_to_agent_log_file(
            f'Error encountered when attempting to send the Agent heart beat end marker. Error: {e}',
            level=logging.ERROR,
        )
    agent_logging.close_out(logging_config)

    return success


def potentially_send_data(config, creds, successful=True) -> bool:
    if config.run_mode_includes_send:
        successful &= send_data(config, creds, successful)
    else:
        logger.info(
            f'\nSkipping send_data because run_mode is "{config.run_mode}"\n'
            f'You can now inspect the downloaded data in {config.outdir}\n'
            f'To send this data to Jellyfish, use "-m send_only -od {config.outdir}"'
        )

    return successful


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

JellyfishEndpointInfo = namedtuple(
    'JellyfishEndpointInfo', ['jira_info', 'git_instance_info', 'jf_options']
)

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
            logger.error(
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
        logger.error('ERROR: JELLYFISH_API_TOKEN not found in the environment.')
        raise BadConfigException()

    jira_username = os.environ.get('JIRA_USERNAME', None)
    jira_password = os.environ.get('JIRA_PASSWORD', None)
    jira_bearer_token = os.environ.get('JIRA_BEARER_TOKEN', None)

    # obtain git slug to credentials
    git_instance_to_creds = {
        git_config.git_instance_slug: _get_git_instance_to_creds(git_config)
        for git_config in config.git_configs
    }
    if len(set(list(token.values())[0] for token in git_instance_to_creds.values())) < len(
        git_instance_to_creds
    ):
        logger.warning(
            'WARNING: Tokens for each git instance provided are not unique. You will see better performance by configuring '
            'git instances for the same provider with separate tokens that have independent rate-limits.'
        )

    jira_username_pass_missing = bool(not (jira_username and jira_password))
    jira_bearer_token_missing = bool(not jira_bearer_token)
    if config.jira_url and jira_username_pass_missing and jira_bearer_token_missing:
        logger.error(
            'ERROR: Jira credentials not found. Set environment variables JIRA_USERNAME and JIRA_PASSWORD or JIRA_BEARER_TOKEN.'
        )
        raise BadConfigException()

    return UserProvidedCreds(
        jellyfish_api_token, jira_username, jira_password, jira_bearer_token, git_instance_to_creds
    )


def obtain_jellyfish_endpoint_info(config, creds):
    base_url = config.jellyfish_api_base
    resp = requests.get(
        f'{base_url}/endpoints/agent/pull-state',
        headers={'Jellyfish-API-Token': creds.jellyfish_api_token},
    )

    if not resp.ok:
        # Base URL is our jellyfish URL. NOT sensitive client data
        logger.error(
            f"ERROR: Couldn't get agent config info from {base_url}/agent/pull-state "
            f'using provided JELLYFISH_API_TOKEN (HTTP {resp.status_code})'
        )
        raise BadConfigException()

    agent_config_from_api = resp.json()
    jira_info = agent_config_from_api.get('jira_info')
    git_instance_info = agent_config_from_api.get('git_instance_info')
    jf_options = agent_config_from_api.get("jf_options", {})

    # if no git info has returned from the endpoint, then an instance may not have been provisioned
    if len(config.git_configs) > 0 and not len(git_instance_info.values()):
        logger.error(
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
                logger.error(
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
        logger.error(
            'ERROR: A single Git instance has been configured, but multiple Git instances were returned '
            'from the Jellyfish API -- please contact Jellyfish'
        )
        raise BadConfigException()

    # if multi git instance is configured in the YAML, assert there is a valid git_instance_slug
    if len(config.git_configs) > 1:
        for git_config in config.git_configs:
            if not git_config.git_instance_slug:
                logger.error(
                    'ERROR: Must specify git_instance slug in multi-git mode -- '
                    'please check your configuration or contact Jellyfish'
                )
                raise BadConfigException()

            if git_config.git_instance_slug not in git_instance_info:
                logger.error(
                    f'ERROR: Invalid `instance_slug` {git_config.git_instance_slug} in configuration. -- '
                    'please check your configuration or contact Jellyfish'
                )
                raise BadConfigException()

    return JellyfishEndpointInfo(jira_info, git_instance_info, jf_options)


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def generate_manifests(config, creds, jellyfish_endpoint_info):
    manifests: list[Manifest] = []

    company_info = get_company_info(config, creds)

    company_slug = company_info.get('company_slug')

    # temporarily disable Jira manifests

    # Create and add Jira Manifest
    # if config.jira_url:
    #     logger.info('Attempting to generate Jira Manifest...')
    #     try:
    #         jira_manifest = create_jira_manifest(
    #             company_slug=company_slug, config=config, creds=creds
    #         )
    #         if jira_manifest:
    #             logger.info('Successfully created Jira Manifest')
    #             manifests.append(jira_manifest)
    #         else:
    #             logger.warning('create_jira_manifest returned a None Type.')
    #     except Exception as e:
    #         logger.debug(
    #             f'Error encountered when generating jira manifest. Error: {e}', exc_info=True
    #         )
    # else:
    #     logger.info('No Jira config detected, skipping Jira manifest generation')

    if config.git_configs:
        try:
            logger.info('Attempting to generate Git Manifests...')
            # Create and add Git Manifests
            manifests += create_git_manifests(
                company_slug=company_slug,
                creds=creds,
                config=config,
                endpoint_git_instances_info=jellyfish_endpoint_info.git_instance_info,
            )
        except Exception as e:
            logging_helper.send_to_agent_log_file(
                f'Error encountered when generating git manifests. Error: {e}', level=logging.ERROR
            )
            logging_helper.send_to_agent_log_file(traceback.format_exc(), level=logging.ERROR)
    else:
        logger.info('No Git Configuration detection, skipping Git manifests generation',)

    logger.info(f'Attempting upload of {len(manifests)} manifest(s) to Jellyfish...')

    for manifest in manifests:
        # Always send Manifest data to S3
        manifest.upload_to_s3(
            jellyfish_api_base=config.jellyfish_api_base,
            jellyfish_api_token=creds.jellyfish_api_token,
        )

    logger.info(f'Successfully uploaded {len(manifests)} manifest(s) to Jellyfish!')


# far more straightforward than real bin packing because we have a set number of bins to start
def _pack_bins(num_bins: int, packing_items: list[ProjectMetadata]) -> list:
    bins = [[] for i in range(num_bins)]
    bin_size = [
        0 for i in range(num_bins)
    ]  # size is based on number of *repos* in a bin, not projects

    for item in packing_items:
        smallest_bin_index = min(range(len(bin_size)), key=bin_size.__getitem__)
        bins[smallest_bin_index].append(item)
        bin_size[smallest_bin_index] += item.num_repos

    return bins


def distribute_repos_between_workers(git_configs, metadata_by_project):
    logger.info(f'Starting to distribute projects/ repos between workers')
    projects_to_be_distributed = []
    configs_can_be_distributed = set()
    configs_cannot_be_distributed = set()
    for project in metadata_by_project:
        if len(metadata_by_project[project].valid_creds) > 1:
            for prefix in metadata_by_project[project].valid_creds:
                configs_can_be_distributed.add(prefix)
            projects_to_be_distributed.append(metadata_by_project[project])
        else:
            configs_cannot_be_distributed.add(metadata_by_project[project].valid_creds[0])

    logger.info(
        f'{len(configs_can_be_distributed)} of {len(git_configs)} '
        f'configs can have projects distributed'
    )
    if len(configs_can_be_distributed) < 1:
        logger.info(f'No configs can be distributed, moving on')
        return git_configs
    projects_to_be_distributed.sort(
        key=lambda p: p.num_repos, reverse=True
    )  # desc so that largest project goes first
    # naively assume only distributing between one set (ie n number of 1:1 cred:org projects and y number cred x 2:org)
    # will be feature flagged by company (so no guesswork)
    num_bins = len(projects_to_be_distributed[0].valid_creds)
    logger.info(f'Before packing:')
    for git_config in git_configs:
        logger.info(f'{git_config.git_instance_slug}: {len(git_config.git_include_projects)}')

    bins = _pack_bins(num_bins=num_bins, packing_items=projects_to_be_distributed)

    bin_index = 0
    for prefix in configs_can_be_distributed:
        for git_config in git_configs:
            if git_config.creds_envvar_prefix == prefix:
                git_config.git_include_projects = [
                    project.project_name for project in bins[bin_index]
                ]
                bin_index += 1

    logger.info(f'After packing:')
    for git_config in git_configs:
        logger.info(f'{git_config.git_instance_slug}: {len(git_config.git_include_projects)}')

    return git_configs


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_data(config, creds, endpoint_jira_info, endpoint_git_instances_info, jf_options):
    download_data_status = []

    if config.jira_url:
        logger.info('Obtained Jira configuration, attempting download...',)
        jira_connection = get_basic_jira_connection(config, creds)
        if config.run_mode_is_print_all_jira_fields:
            print_all_jira_fields(config, jira_connection)
        if endpoint_jira_info.get('use_jf_ingest_for_jira', False):
            try:
                logger.info(f'Attempting to use JF Ingest for Jira Ingestion')
                ingest_config = get_ingest_config(
                    config=config,
                    creds=creds,
                    endpoint_jira_info=endpoint_jira_info,
                    endpoint_git_instances_info=endpoint_git_instances_info,
                    jf_options=jf_options,
                )
                success = load_and_push_jira_to_s3(ingest_config)
                success_status_str = 'success' if success else 'failed'
                download_data_status.append({'type': 'Jira', 'status': success_status_str})
            except Exception as e:
                logger.error(
                    'Error encountered when downloading Jira data. '
                    'This Jira submission will be marked as failed. '
                    f'Error: {e}'
                )
                logging_helper.send_to_agent_log_file(traceback.format_exc(), level=logging.ERROR)
                download_data_status.append({'type': 'Jira', 'status': 'failed'})
        else:
            download_data_status.append(
                load_and_dump_jira(config, endpoint_jira_info, jira_connection)
            )

    if len(config.git_configs) == 0:
        return download_data_status

    # git downloading is parallelized by the number of configurations.
    futures = []
    with ThreadPoolExecutor(max_workers=config.git_max_concurrent) as executor:
        if jf_options.get('normalize_project_distribution', False):
            try:
                metadata_by_project = validate_num_repos(
                    git_configs=config.git_configs, creds=creds
                )
                # TODO: use a queue for threads to pull from & support include/exclude repos
                git_configs = distribute_repos_between_workers(
                    config.git_configs, metadata_by_project
                )
            except Exception as e:
                logger.warning(
                    f'Exception during project distribution, using default config and moving on. Got: {e}'
                )
        else:
            git_configs = config.git_configs

        for git_config in git_configs:
            logger.info(
                f'Obtained {git_config.git_provider}:{git_config.git_instance_slug} configuration, attempting download '
                + f'in parallel with {config.git_max_concurrent} workers, '
                + f'for {len(git_config.git_include_projects)} projects'
                if len(config.git_configs) > 1
                else f"Starting Git download for {len(config.git_configs)} provided git configurations",
            )

            ingest_config = get_ingest_config(
                config=config,
                creds=creds,
                endpoint_jira_info=endpoint_jira_info,
                endpoint_git_instances_info=endpoint_git_instances_info,
                jf_options=jf_options,
            )
            futures.append(
                executor.submit(
                    _download_git_data,
                    git_config,
                    config,
                    creds,
                    endpoint_git_instances_info,
                    len(config.git_configs) > 1,
                    jf_options,
                    ingest_config,
                )
            )

    return download_data_status + [f.result() for f in futures]


def _download_git_data(
    git_config,
    config,
    creds,
    endpoint_git_instances_info,
    is_multi_git_config,
    jf_options,
    jf_ingest_config: IngestionConfig,
) -> dict:
    if is_multi_git_config:
        instance_slug = git_config.git_instance_slug
        instance_info = endpoint_git_instances_info.get(instance_slug)
        instance_creds = creds.git_instance_to_creds.get(instance_slug)
    else:
        # support legacy single-git support, which assumes only one available git instance
        instance_info = list(endpoint_git_instances_info.values())[0]
        instance_creds = list(creds.git_instance_to_creds.values())[0]

    git_connection = get_git_client(
        git_config,
        instance_creds,
        skip_ssl_verification=config.skip_ssl_verification,
        instance_info=instance_info,
    )
    return load_and_dump_git(
        config=git_config,
        endpoint_git_instance_info=instance_info,
        outdir=config.outdir,
        compress_output_files=config.compress_output_files,
        git_connection=git_connection,
        jf_options=jf_options,
        jf_ingest_config=jf_ingest_config,
    )


def get_timestamp_from_outdir(outdir: str):
    _, timestamp = os.path.split(outdir)
    return timestamp


def send_data(config, creds, successful=True):
    timestamp = get_timestamp_from_outdir(config.outdir)

    def get_signed_url(files):
        base_url = config.jellyfish_api_base
        headers = {'Jellyfish-API-Token': creds.jellyfish_api_token}
        payload = {'files': files, 'ingestType': IngestionType.AGENT}

        r = requests.post(
            f'{base_url}/endpoints/ingest/signed-url?timestamp={timestamp}',
            headers=headers,
            json=payload,
        )
        r.raise_for_status()

        return r.json()['signed_urls']

    thread_exceptions = []

    def upload_file_from_thread(filename, path_to_obj, signed_url):
        try:
            upload_file(filename, path_to_obj, signed_url, config_outdir=config.outdir)
        except Exception as e:
            thread_exceptions.append(e)
            logging_helper.log_standard_error(
                logging.ERROR, msg_args=[filename], error_code=3000, exc_info=True,
            )

    # Compress any not yet compressed files before sending
    for fname in glob(f'{config.outdir}/*.json'):
        logger.info(f'Compressing {fname}')
        with open(fname, 'rb') as f_in:
            with gzip.open(f'{fname}.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(fname)

    logger.info('Sending data to Jellyfish... ')

    # obtain file names from the directory
    _, directories, filenames = next(os.walk(config.outdir))

    # Remove the log file from filenames that will get uploaded in bulk
    # We want to save the jf_agent.log file and upload it last, to ensure
    # we are capturing as much information as possible
    filenames.remove(agent_logging.LOG_FILE_NAME)

    # get the full file paths for each of the immediate
    # subdirectories (we're assuming only a single level)
    for directory in directories:
        # SKIP OVER THE JIRA DIRECTORY, WHICH IS UPLOADED VIA JF INGEST
        # TODO: The GIT directories will also be uploaded via ingest,
        # but for now they are handled via this 'legacy' way
        if directory.lower() == 'jira':
            continue
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

    logger.info(f'Starting {len(threads)} threads')

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    if any(thread_exceptions):
        # Run through exceptions and inject them into the agent log
        for exception in thread_exceptions:
            logging_helper.log_standard_error(
                logging.ERROR, error_code=0000, msg_args=[exception], exc_info=True
            )
        logger.error(
            'ERROR: not all files uploaded to S3. Files have been saved locally. Once connectivity issues are resolved, try running the Agent in send_only mode.'
        )
        successful = False

    # If sending agent config flag is on, upload config.yml to s3 bucket
    if config.send_agent_config:
        config_file_dict = get_signed_url(['config.yml'])['config.yml']
        upload_file(
            'config.yml',
            config_file_dict['s3_path'],
            config_file_dict['url'],
            local=True,
            config_outdir=config.outdir,
        )

    # Log this information before we upload the log file.
    logger.info(f'Agent run succeeded: {successful}')

    # Upload log files as last step before uploading the .done file
    log_file_dict = get_signed_url([agent_logging.LOG_FILE_NAME])[agent_logging.LOG_FILE_NAME]
    upload_file(
        agent_logging.LOG_FILE_NAME,
        log_file_dict['s3_path'],
        log_file_dict['url'],
        config_outdir=config.outdir,
    )

    if successful:
        # creating .done file, only on success
        done_file_path = f'{os.path.join(config.outdir, ".done")}'
        Path(done_file_path).touch()
        done_file_dict = get_signed_url(['.done'])['.done']
        upload_file(
            '.done', done_file_dict['s3_path'], done_file_dict['url'], config_outdir=config.outdir
        )

    return successful


def get_issues_to_scan_from_jellyfish(config, creds, updated_within_last_x_months):
    base_url = config.jellyfish_api_base
    api_token = creds.jellyfish_api_token

    params = {}
    if updated_within_last_x_months:
        params.update({'monthsback': updated_within_last_x_months})

    logger.info('Fetching Jira issues that are missing Git repo data in Jellyfish...')

    resp = requests.get(
        f'{base_url}/endpoints/agent/unlinked-dev-issues',
        headers={'Jellyfish-API-Token': api_token},
        params=params,
    )

    # try and grab any specific error messages sent over
    try:
        data = resp.json()
        logger.info(data.get('message', ''))
    except json.decoder.JSONDecodeError:
        logger.error(
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
        success = main()
        if not success:
            sys.exit(1)
    except BadConfigException:
        logger.error('ERROR: Bad config; see earlier messages')
        sys.exit(1)
