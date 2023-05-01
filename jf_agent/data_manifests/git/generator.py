import logging
from jf_agent import agent_logging
from jf_agent.config_file_reader import GitConfig

from jf_agent.data_manifests.git.adapters.manifest_adapter import ManifestAdapter

from jf_agent.data_manifests.git.manifest import (
    GitDataManifest,
    GitRepoManifest,
    GitTeamManifest,
    GitUserManifest,
)
from jf_agent.data_manifests.git.adapters.github import GithubManifestGenerator
from jf_agent.data_manifests.manifest import ManifestSource
from jf_agent.git import get_git_client

logger = logging.getLogger(__name__)


class UnsupportedGitProvider(Exception):
    pass


def create_manifests(
    company_slug: str, creds: dict, config: dict, verbose: bool = False
) -> list[GitDataManifest]:
    manifests: list[GitDataManifest] = []
    git_configs: list[GitConfig] = config.git_configs
    for git_config in git_configs:
        try:
            users_count = 0
            teams_count = 0
            repo_manifests: list[GitRepoManifest] = []
            user_manifests: list[GitUserManifest] = []
            team_manifests: list[GitTeamManifest] = []
            for org in git_config.git_include_projects:
                agent_logging.log_and_print(
                    logger,
                    logging.INFO,
                    f'Processing git instance {git_config.git_instance_slug} for company {company_slug} under github org {org}',
                )

                if not git_config.git_instance_slug:
                    agent_logging.log_and_print(
                        logger,
                        logging.ERROR,
                        f'Git instance for company {company_slug} was detected as NONE. The manifest for this instance will not be processed or uploaded',
                    )
                    continue

                instance_creds = creds.git_instance_to_creds.get(git_config.git_instance_slug)
                manifest_adapter: ManifestAdapter = get_manifest_adapter(
                    company_slug=company_slug,
                    git_creds=instance_creds,
                    git_config=git_config,
                    org=org,
                )

                repos_count = manifest_adapter.get_repos_count()
                agent_logging.log_and_print(
                    logger,
                    logging.INFO,
                    f'Processing {repos_count} repos {"including Branches and Pull Request" if verbose else ""}',
                )
                for repo_manifest in manifest_adapter.get_all_repo_data():
                    if verbose:
                        # Process Branches for Repo
                        branch_generator = manifest_adapter.get_all_branch_data(
                            repo_name=repo_manifest.repository_name
                        )
                        if branch_generator:
                            repo_manifest.branch_manifests += [
                                branch_manifest for branch_manifest in branch_generator
                            ]

                        pr_generator = manifest_adapter.get_all_pr_data(
                            repo_name=repo_manifest.repository_name
                        )

                        # Process PRs for Repo
                        if pr_generator:
                            repo_manifest.pull_request_manifests += [
                                pr_manifest for pr_manifest in pr_generator
                            ]

                    repo_manifests.append(repo_manifest)

                agent_logging.log_and_print(logger, logging.INFO, 'Done processing Repos')

                user_count = manifest_adapter.get_users_count()
                agent_logging.log_and_print(logger, logging.INFO, f'Processing {user_count} users')
                user_manifests += [
                    user_manifest for user_manifest in manifest_adapter.get_all_user_data()
                ]
                agent_logging.log_and_print(logger, logging.INFO, 'Done processing Users')

                team_count = manifest_adapter.get_teams_count()
                agent_logging.log_and_print(logger, logging.INFO, f'Processing {team_count} teams')
                team_manifests += [
                    team_manifest for team_manifest in manifest_adapter.get_all_team_data()
                ]
                agent_logging.log_and_print(logger, logging.INFO, 'Done processing Teams')

                users_count += user_count
                teams_count += team_count

            manifests.append(
                GitDataManifest(
                    data_source=ManifestSource.remote,
                    company=company_slug,
                    instance=git_config.git_instance_slug,
                    org=org,
                    users_count=users_count,
                    teams_count=teams_count,
                    repos_count=len(repo_manifests),
                    repo_manifests=repo_manifests,
                    user_manifests=user_manifests,
                    team_manifests=team_manifests,
                )
            )
        except UnsupportedGitProvider as e:
            logger.error(
                f'An exception happened when creating {type} manifest for {git_config.git_instance_slug}. Err: {e}'
            )

    return manifests


def get_manifest_adapter(
    company_slug: str, git_creds: dict, git_config: GitConfig, org: str,
):
    instance = git_config.git_instance_slug
    if git_config.git_provider != 'github':
        raise UnsupportedGitProvider(
            f'Currently only instances of source github are supported, cannot process instance {instance}'
        )
    return GithubManifestGenerator(
        token=git_creds['github_token'], company=company_slug, instance=instance, org=org
    )
