import logging
import os
import psutil
import shutil

import requests

from jf_agent.config_file_reader import get_jira_ingest_config
from jf_agent.data_manifests.git.generator import get_instance_slug
from jf_agent.git import get_git_client, get_nested_repos_from_git, GithubGqlClient
from jf_ingest.validation import validate_jira, GitHealthCheckResult, JiraHealthCheckResult, IngestionHealthCheckResult, IngestionType


logger = logging.getLogger(__name__)


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


def full_validate(config, creds, jellyfish_endpoint_info) -> IngestionHealthCheckResult:
    """
    Runs the full validation suite.
    """

    jira_healthcheck_result: JiraHealthCheckResult = None
    git_healthcheck_result: GitHealthCheckResult = None

    logger.info('Validating configuration...')

    # Check for Jira credentials
    if config.jira_url and (
            (creds.jira_username and creds.jira_password) or creds.jira_bearer_token
    ):
        try:
            ingest_config = get_jira_ingest_config(config, creds)

            jira_healthcheck_result = validate_jira(ingest_config)

        # Probably few/no cases that we would hit an exception here, but we want to catch them if there are any
        # We will continue to validate git but will indicate Jira config failed.
        except Exception as e:
            print(f"Failed to validate Jira due to exception of type {e.__class__.__name__}!")

            # Printing this to stdout rather than logger in case the exception has any sensitive info.
            print(e)

    else:
        logger.info("\nNo Jira URL or credentials provided, skipping Jira validation...")

    # Check for Git configs
    if config.git_configs:
        try:
            git_healthcheck_result = validate_git(config, creds, jellyfish_endpoint_info.git_instance_info)

        except Exception as e:
            print(f"Failed to validate Git due to exception of type {e.__class__.__name__}!")

            # Printing this to stdout rather than logger in case the exception has any sensitive info.
            print(e)

    else:
        logger.info("\nNo Git configs provided, skipping Git validation...")

    # Finally, display memory usage statistics.
    validate_memory(config)

    healthcheck_result: IngestionHealthCheckResult = IngestionHealthCheckResult(ingestion_type=IngestionType.AGENT,
                                                                                git_healthcheck_result=git_healthcheck_result,
                                                                                jira_healthcheck_result=jira_healthcheck_result)

    if config.skip_healthcheck_upload:
        logger.info("skip_healthcheck_upload is set to True, this healthcheck report will NOT be uploaded!")
    else:
        upload_to_s3(config.jellyfish_api_base, creds.jellyfish_api_token, healthcheck_result)

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


def validate_git(config, creds, endpoint_git_instances_info) -> list[GitHealthCheckResult]:
    """
    Validates git config and credentials.
    """

    git_configs = config.git_configs

    healthcheck_result_list = []

    for i, git_config in enumerate(git_configs, start=1):
        instance_slug = get_instance_slug(git_config, len(git_configs) > 1, endpoint_git_instances_info)

        healthcheck_result = GitHealthCheckResult(successful=True, instance_slug=instance_slug)

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

            healthcheck_result.accessible_projects_and_repos = project_repo_dict

            included_inaccessible_repos = []
            for repo in git_config.git_include_repos:
                # Messy: GitLab repos are specified as ints, not strings
                if type(repo) == int:
                    def comp_func(repo):
                        return repo not in all_repos

                else:
                    def comp_func(repo):
                        return repo.lower() not in set(n.lower() for n in all_repos)

                if comp_func(repo):
                    print(
                        f"  WARNING: {repo} is explicitly defined as an included repo, but agent doesn't seem"
                        f" to see this repository -- possibly missing permissions."
                    )
                    included_inaccessible_repos.append(repo)
            if included_inaccessible_repos:
                healthcheck_result.successful = False
                healthcheck_result.included_inaccessible_repos = included_inaccessible_repos

        except Exception as e:
            print(f"Git connection unsuccessful! Exception: {e}")
            healthcheck_result.successful = False

        healthcheck_result_list.append(healthcheck_result)

    return healthcheck_result_list


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


def upload_to_s3(jellyfish_api_base: str, jellyfish_api_token: str, healthcheck_result: IngestionHealthCheckResult) -> None:
    headers = {'Jellyfish-API-Token': jellyfish_api_token, 'content-encoding': 'gzip'}

    logger.info(f'Attempting to upload healthcheck result to s3...')

    r = requests.post(
        f'{jellyfish_api_base}/endpoints/agent/upload_healthcheck',
        headers=headers,
        json=healthcheck_result.to_dict(),
    )

    r.raise_for_status()

    logger.info(f'Successfully uploaded healthcheck result to s3!')
