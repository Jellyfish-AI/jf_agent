import logging
import traceback
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

logger = logging.getLogger(__name__)


class UnsupportedGitProvider(Exception):
    pass


def create_manifests(
    company_slug: str,
    creds: dict,
    config: dict,
    endpoint_git_instances_info: dict,
    verbose: bool = False,
) -> list[GitDataManifest]:
    manifests: list[GitDataManifest] = []
    git_configs: list[GitConfig] = config.git_configs
    is_multi_git_config = len(git_configs) > 1

    # Iterate over each git config within the config yaml
    for git_config in git_configs:
        if is_multi_git_config:
            instance_slug = git_config.git_instance_slug
            instance_info = endpoint_git_instances_info.get(instance_slug)
            instance_creds = creds.git_instance_to_creds.get(instance_slug)
        else:
            # support legacy single-git support, which assumes only one available git instance
            instance_info = list(endpoint_git_instances_info.values())[0]
            instance_creds = list(creds.git_instance_to_creds.values())[0]
            instance_slug: str = instance_info['slug']

        try:
            # If the git config doesn't have a git instance, do not generate a manifest
            if not instance_slug:
                agent_logging.log_and_print(
                    logger,
                    logging.WARNING,
                    f'Git instance for company {company_slug} was detected as NONE. The manifest for this instance will not be processed or uploaded. This should NOT affect your agent upload',
                )
                continue

            agent_logging.log_and_print(
                logger, logging.INFO, f'Generating manifest for instance {instance_slug}'
            )
            for org in git_config.git_include_projects:
                agent_logging.log_and_print(
                    logger,
                    logging.INFO,
                    f'Processing git instance {instance_slug} for company {company_slug} under github org {org}',
                )
                manifest_adapter: ManifestAdapter = get_manifest_adapter(
                    company_slug=company_slug,
                    git_creds=instance_creds,
                    git_config=git_config,
                    instance=instance_slug,
                    org=org,
                )

                repo_manifests: list[GitRepoManifest] = []
                user_manifests: list[GitUserManifest] = []
                team_manifests: list[GitTeamManifest] = []

                # Process Repos
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

                # Process Users
                users_count = manifest_adapter.get_users_count()
                agent_logging.log_and_print(logger, logging.INFO, f'Processing {users_count} users')
                user_manifests += [
                    user_manifest for user_manifest in manifest_adapter.get_all_user_data()
                ]
                agent_logging.log_and_print(logger, logging.INFO, 'Done processing Users')

                # Process Teams
                teams_count = manifest_adapter.get_teams_count()
                agent_logging.log_and_print(logger, logging.INFO, f'Processing {teams_count} teams')
                team_manifests += [
                    team_manifest for team_manifest in manifest_adapter.get_all_team_data()
                ]
                agent_logging.log_and_print(logger, logging.INFO, 'Done processing Teams')

                manifests.append(
                    GitDataManifest(
                        data_source=ManifestSource.remote,
                        company=company_slug,
                        instance=instance_slug,
                        org=org,
                        users_count=users_count,
                        teams_count=teams_count,
                        repos_count=repos_count,
                        repo_manifests=repo_manifests,
                        user_manifests=user_manifests,
                        team_manifests=team_manifests,
                    )
                )
        except UnsupportedGitProvider as e:
            agent_logging.log_and_print(
                logger,
                logging.WARNING,
                'Unsupported Git Provider exception encountered. '
                f'This shouldn\'t affect your agent upload. Error: {e}',
            )
        except Exception as e:
            agent_logging.log_and_print(
                logger,
                logging.WARNING,
                'An exception happened when creating manifest. This shouldn\'t affect your agent upload. '
                f'Exception: {e}',
            )

    return manifests


def get_manifest_adapter(
    company_slug: str, git_creds: dict, git_config: GitConfig, instance: str, org: str,
):
    if git_config.git_provider != 'github':
        raise UnsupportedGitProvider(
            f'Currently only instances of source github are supported, cannot process instance {instance} which has git_provider type {git_config.git_provider}'
        )
    return GithubManifestGenerator(
        token=git_creds['github_token'], company=company_slug, instance=instance, org=org
    )
