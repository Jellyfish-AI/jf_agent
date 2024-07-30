import logging
import os
import psutil
import shutil

import requests

from jf_agent.data_manifests.git.generator import get_instance_slug
from jf_agent.config_file_reader import get_ingest_config
from jf_agent.git import get_git_client, get_nested_repos_from_git, GithubGqlClient
from jf_ingest.validation import (
    validate_jira,
    GitConnectionHealthCheckResult,
    JiraConnectionHealthCheckResult,
    IngestionHealthCheckResult,
    IngestionType,
)

from jf_agent.util import upload_file

from jf_agent import write_file

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

    jira_connection_healthcheck_result: JiraConnectionHealthCheckResult = None
    git_connection_healthcheck_result: GitConnectionHealthCheckResult = None

    logger.info('Validating configuration...')

    try:
        ingest_config = get_ingest_config(
            config,
            creds,
            jellyfish_endpoint_info.jira_info,
            jellyfish_endpoint_info.git_instance_info,
            jf_options=jellyfish_endpoint_info.jf_options,
        )
        if ingest_config.jira_config and not skip_jira:
            jira_connection_healthcheck_result = validate_jira(ingest_config.jira_config)
        else:
            msg = 'Skipping Jira Validation.'
            logger.info(f"{'' if skip_jira else 'No Jira config found'}. " + msg)

    # Probably few/no cases that we would hit an exception here, but we want to catch them if there are any
    # We will continue to validate git but will indicate Jira config failed.
    except Exception as e:
        print(f"Failed to validate Jira due to exception of type {e.__class__.__name__}!")

        # Printing this to stdout rather than logger in case the exception has any sensitive info.
        print(e)

    # Check for Git configs
    if config.git_configs and not skip_git:
        try:
            git_connection_healthcheck_result = validate_git(
                config, creds, jellyfish_endpoint_info.git_instance_info
            )

        except Exception as e:
            print(f"Failed to validate Git due to exception of type {e.__class__.__name__}!")

            # Printing this to stdout rather than logger in case the exception has any sensitive info.
            print(e)

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
            config.jellyfish_api_base, creds.jellyfish_api_token, config_outdir
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


def validate_git(
    config, creds, endpoint_git_instances_info
) -> list[GitConnectionHealthCheckResult]:
    """
    Validates git config and credentials.
    """

    git_configs = config.git_configs

    healthcheck_result_list = []

    for i, git_config in enumerate(git_configs, start=1):
        instance_slug = get_instance_slug(git_config, endpoint_git_instances_info)

        successful = True

        included_inaccessible_repos_list = git_config.git_include_repos

        accessible_projects_and_repos = {}

        print(f"\nGit details for instance {i}/{len(git_configs)}:")
        print(f"  Instance slug: {instance_slug}")
        print(f"  Provider: {git_config.git_provider}")
        print(f"  Included projects: {git_config.git_include_projects}")
        if len(git_config.git_exclude_projects) > 0:
            print(f"  Excluded projects: {git_config.git_exclude_projects}")
        print(f"  Included repos: {git_config.git_include_repos}")
        if len(git_config.git_exclude_repos) > 0:
            print(f"  Excluded repos: {git_config.git_exclude_repos}")
        if len(git_config.git_include_branches) > 0:
            print(f"  Included Branches: {git_config.git_include_branches}")

        print('==> Testing Git connection...')

        try:
            client = get_git_client(
                git_config,
                list(creds.git_instance_to_creds.values())[i - 1],
                skip_ssl_verification=config.skip_ssl_verification,
            )

            project_repo_dict = get_nested_repos_from_git(client, git_config)

            accessible_projects_and_repos = project_repo_dict

            all_repos = sum(project_repo_dict.values(), [])

            if not all_repos:
                print(
                    " =============================================================================/n \033[91mERROR: No projects and repositories available to agent: Please Check Configuration\033[0m /n ============================================================================="
                )
                continue

            print("  All projects and repositories available to agent:")
            for project_name, repo_list in project_repo_dict.items():
                print(f"  -- {project_name}")

                for repo in repo_list:
                    print(f"    -- {repo}")

            included_inaccessible_repos_list = [
                r
                for r in included_inaccessible_repos_list
                if not _check_repo_included(r, all_repos)
            ]

            if included_inaccessible_repos_list:
                successful = False
                print(
                    f"  WARNING: the following repos are explicitly defined as included repos, but agent doesn't seem"
                    f" to see this repository -- possibly missing permissions."
                )
                for inaccessible_repo in included_inaccessible_repos_list:
                    print(f"    - {inaccessible_repo}")

        except Exception as e:
            print(f"Git connection unsuccessful! Exception: {e}")
            successful = False

        healthcheck_result = GitConnectionHealthCheckResult(
            successful=successful,
            instance_slug=instance_slug,
            included_inaccessible_repos=included_inaccessible_repos_list,
            accessible_projects_and_repos=accessible_projects_and_repos,
        )

        healthcheck_result_list.append(healthcheck_result)

    return healthcheck_result_list


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

        output_dir_size = os.popen(f'du -hs {config.outdir}').readlines()[0].split("\t")[0]
        usage = shutil.disk_usage(config.outdir)

        print(
            f'  Disk usage for jf_agent/output: {int(round(usage.used / (1024 ** 3)))} GB / {int(round(usage.total / 1024 ** 3))} GB'
        )
        print(f"  Size of jf_agent/output dir: {output_dir_size}")

    except Exception as e:
        print(f"  ERROR: Could not obtain memory and/or disk usage information. {e}")
        return False


def get_healthcheck_signed_urls(
    jellyfish_api_base: str, jellyfish_api_token: str, files: list[str]
):
    headers = {'Jellyfish-API-Token': jellyfish_api_token}

    payload = {'files': files}

    r = requests.post(
        f'{jellyfish_api_base}/endpoints/agent/healthcheck/signed-url',
        headers=headers,
        json=payload,
    )
    r.raise_for_status()

    return r.json()['signed_urls']


def submit_health_check_to_jellyfish(
    jellyfish_api_base: str, jellyfish_api_token: str, config_outdir: str
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
