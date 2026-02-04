import logging
import os
import shutil
import traceback
from typing import Optional

import psutil
import requests
from jf_ingest.validation import (
    GitConnectionHealthCheckResult,
    IngestionHealthCheckResult,
    IngestionType,
    JiraConnectionHealthCheckResult,
    validate_git,
    validate_jira,
)

from jf_agent import write_file
from jf_agent.config_file_reader import get_ingest_config
from jf_agent.data_manifests.git.generator import get_instance_slug
from jf_agent.git import GithubGqlClient, get_git_client, get_nested_repos_from_git
from jf_agent.util import upload_file

logger = logging.getLogger(__name__)

HEALTHCHECK_JSON_FILENAME = "healthcheck.json"

# NOTE: Pretty much all 'logging' calls here should use PRINT
# and not logger.info. Logger.info will log to stdout AND
# to our log file. We do NOT want to log any passwords or usernames.
# To be extra safe, use print instead of logger.log within validation


class ProjectMetadata:
    project_name = ""
    valid_creds: list[str] = []
    num_repos: int = 0

    def __init__(self, project_name="", valid_creds=[], num_repos=0):
        self.project_name = project_name
        self.valid_creds = valid_creds
        self.num_repos = num_repos

    def __str__(self):
        return f'project {self.project_name} accessible with {self.valid_creds} containing {self.num_repos} repos'


def full_validate(
    config,
    creds,
    jellyfish_endpoint_info,
    skip_jira: bool = False,
    skip_git: bool = False,
    upload: bool = True,
) -> IngestionHealthCheckResult:
    """
    Runs the full validation suite.
    """

    jira_connection_healthcheck_result: Optional[JiraConnectionHealthCheckResult] = None
    git_connection_healthcheck_result: Optional[list[GitConnectionHealthCheckResult]] = None

    logger.info('Validating configuration...')

    ingest_config = None
    try:
        ingest_config = get_ingest_config(
            config,
            creds,
            jellyfish_endpoint_info.jira_info,
            jellyfish_endpoint_info.git_instance_info,
            jf_options=jellyfish_endpoint_info.jf_options,
        )
    except Exception as e:
        print(f"Failed to validate configuration due to exception of type {e.__class__.__name__}!")
        print(traceback.format_exc())

    if ingest_config:
        if ingest_config.jira_config and not skip_jira:
            jira_connection_healthcheck_result = validate_jira(ingest_config.jira_config)
        else:
            msg = 'Skipping Jira Validation.'
            logger.info(f"{'' if skip_jira else 'No Jira config found'}. " + msg)

        if ingest_config.git_configs and not skip_git:
            git_connection_healthcheck_result = validate_git(ingest_config.git_configs)
        else:
            msg = 'Skipping Git Validation.'
            logger.info(f"{'' if skip_git else 'No Git config found'}. " + msg)

    # Finally, display memory usage statistics.
    validate_memory(config)

    healthcheck_result = IngestionHealthCheckResult(
        ingestion_type=IngestionType.AGENT,
        git_connection_healthcheck=git_connection_healthcheck_result,
        jira_connection_healthcheck=jira_connection_healthcheck_result,
    )

    config_outdir = config.outdir

    with open(f'{config_outdir}/{HEALTHCHECK_JSON_FILENAME}', 'w') as outfile:
        outfile.write(healthcheck_result.to_json())

    if upload:
        submit_health_check_to_jellyfish(
            config.jellyfish_api_base,
            creds.jellyfish_api_token,
            config_outdir,
            config.skip_ssl_verification,
        )
    else:
        logger.info("This healthcheck report will not be uploaded.")

    logger.info("\nDone")

    return healthcheck_result


def validate_num_repos(git_configs, creds):
    metadata_by_project = {}
    for git_config in git_configs:
        for cred_slug in creds.git_instance_to_creds:
            for project in git_config.git_include_projects:
                client = None
                token = creds.git_instance_to_creds.get(cred_slug).get('github_token')
                try:
                    client = GithubGqlClient(base_url=git_config.git_url, token=token)
                    repo_count = client.get_repos_count(login=project)
                    client.session.close()
                    if project in metadata_by_project.keys():
                        metadata_by_project[project].valid_creds.append(cred_slug)
                    else:
                        metadata_by_project[project] = ProjectMetadata(
                            project_name=project, valid_creds=[cred_slug], num_repos=repo_count
                        )
                    msg = (
                        f"credentials preface: {cred_slug} "
                        f"identified {repo_count} repos in instance {git_config.git_instance_slug}/{project}"
                    )
                    logger.info(msg=msg)

                except Exception as e:
                    if client and client.session:
                        client.session.close()
                    print(e)
                    msg = (
                        f"credentials preface: {cred_slug} not valid to config preface: "
                        f"{git_config.git_instance_slug}, got {e}, moving on."
                    )
                    logger.warning(msg=msg)

    return metadata_by_project


def _check_repo_included(repo: str | int, all_repos: list[str]) -> bool:
    """
    Takes in a repo and returns whether it is in the given list of accessible repos
    Handles the gitlab case where repos are specified as ints.

    """
    if type(repo) == int:
        return repo in all_repos
    else:
        return repo.lower() in set(n.lower() for n in all_repos)


def validate_memory(config):
    """
    Displays memory and disk usage statistics.
    """
    print("\nMemory & Disk Usage:")

    try:
        print(
            f"  Available memory: {round(psutil.virtual_memory().available / (1024 * 1024), 2)} MB"
        )

        output_dir_path = os.path.expanduser(config.outdir)
        output_dir_size = _format_bytes(_get_directory_size_bytes(output_dir_path))
        usage = shutil.disk_usage(config.outdir)

        print(
            f'  Disk usage for jf_agent/output: {int(round(usage.used / (1024 ** 3)))} GB / {int(round(usage.total / 1024 ** 3))} GB'
        )
        print(f"  Size of jf_agent/output dir: {output_dir_size}")

    except Exception as e:
        print(f"  ERROR: Could not obtain memory and/or disk usage information. {e}")
        return False


def _get_directory_size_bytes(path: str) -> int:
    total_size = 0
    for root, _, files in os.walk(path):
        for filename in files:
            file_path = os.path.join(root, filename)
            try:
                total_size += os.path.getsize(file_path)
            except OSError:
                # Ignore files that disappear or cannot be accessed
                continue
    return total_size


def _format_bytes(num_bytes: int) -> str:
    units = ["B", "K", "M", "G", "T"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return str(int(size))
            return f"{size:.1f}{unit}"
        size /= 1024


def get_healthcheck_signed_urls(
    jellyfish_api_base: str,
    jellyfish_api_token: str,
    files: list[str],
    skip_ssl_verification: bool = False,
):
    headers = {'Jellyfish-API-Token': jellyfish_api_token}

    payload = {'files': files}

    r = requests.post(
        f'{jellyfish_api_base}/endpoints/agent/healthcheck/signed-url',
        headers=headers,
        json=payload,
        verify=not skip_ssl_verification,
    )
    r.raise_for_status()

    return r.json()['signed_urls']


def submit_health_check_to_jellyfish(
    jellyfish_api_base: str,
    jellyfish_api_token: str,
    config_outdir: str,
    skip_ssl_verification: bool = False,
) -> None:
    """
    Uploads the given IngestionHealthCheckResult to Jellyfish
    """
    logger.info(f'Attempting to upload healthcheck result to s3...')
    agent_log_filename = 'jf_agent.log'
    signed_urls = get_healthcheck_signed_urls(
        jellyfish_api_base=jellyfish_api_base,
        jellyfish_api_token=jellyfish_api_token,
        files=[HEALTHCHECK_JSON_FILENAME, agent_log_filename],
        skip_ssl_verification=skip_ssl_verification,
    )

    # Uploading healthcheck.json
    healthcheck_signed_url = signed_urls[HEALTHCHECK_JSON_FILENAME]
    upload_file(
        HEALTHCHECK_JSON_FILENAME,
        healthcheck_signed_url['s3_path'],
        healthcheck_signed_url['url'],
        config_outdir=config_outdir,
    )

    # Uploading jf_agent.log
    logfile_signed_url = signed_urls[agent_log_filename]
    upload_file(
        agent_log_filename,
        logfile_signed_url['s3_path'],
        logfile_signed_url['url'],
        config_outdir=config_outdir,
    )

    logger.info(f'Successfully uploaded healthcheck result to s3!')
