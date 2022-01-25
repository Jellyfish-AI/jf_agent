import os
import psutil
import shutil

from jf_jira import _get_raw_jira_connection
from jf_jira.jira_download import download_users
from jf_agent.git import get_git_client, get_nested_repos_from_git


def validate_jira(config, creds):
    """
    Validates jira configuration and credentials.
    """

    print('\nJira details:')
    print(f'  URL:      {config.jira_url}')
    print(f'  Username: {creds.jira_username}')
    pw_len = len(creds.jira_password) - 2
    print(f'  Password: {creds.jira_password[:2]:*<{pw_len}}')
    # test Jira connection
    try:
        print('==> Testing Jira connection...')
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
        return False

    # test jira users permission
    try:
        print('==> Testing Jira user browsing permissions...')
        user_count = len(download_users(jira_connection, config.jira_gdpr_active, quiet=True))
        print(f'The agent is aware of {user_count} Jira users.')

    except Exception as e:
        print(
            f'Error downloading users from Jira instance at {config.jira_url}, please verify that this user has the "browse all users" permission. Error: {e}'
        )
        return False

    # test jira project access
    print('==> Testing Jira project permissions...')
    accessible_projects = [p.key for p in jira_connection.projects()]
    print(f'The agent has access to projects {accessible_projects}.')

    if config.jira_include_projects:
        for proj in config.jira_include_projects:
            if proj not in accessible_projects:
                print(f'Error: Unable to access explicitly-included project {proj}.')
                return False


def validate_git(config, creds):
    """
    Validates git config and credentials.
    """
    git_configs = config.git_configs

    for i, git_config in enumerate(git_configs, start=1):
        print(f"\nGit details for instance {i}/{len(git_configs)}:")
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

            print("  All projects and repositories available to agent:")
            for project_name, repo_list in project_repo_dict.items():
                print(f"  -- {project_name}")
                for repo in repo_list:
                    print(f"    -- {repo}")

            for repo in git_config.git_include_repos:
                # Messy: GitLab repos are specified as as ints, not strings
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

        except Exception as e:
            print(f"Git connection unsuccessful! Exception: {e}")
            return False


def validate_memory():
    """
    Displays memory and disk usage statistics.
    """
    print("\nMemory & Disk Usage:")

    try:
        print(
            f"  Available memory: {round(psutil.virtual_memory().available / (1024 * 1024), 2)} MB"
        )

        output_dir_size = os.popen('du -hs ./output').readlines()[0].split("\t")[0]
        usage = shutil.disk_usage('./output')

        print(
            f'  Disk usage for jf_agent/output: {int(round(usage.used / (1024 ** 3)))} GB / {int(round(usage.total / 1024 ** 3))} GB'
        )
        print(f"  Size of jf_agent/output dir: {output_dir_size}")

    except Exception as e:
        print(f"  ERROR: Could not obtain memory and/or disk usage information. {e}")
        return False
