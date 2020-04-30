import pytz
from typing import List
from dataclasses import dataclass
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import logging
from stashy.client import Stash

from jf_agent.session import retry_session
from jf_agent.git.bitbucket_cloud_client import BitbucketCloudClient
from jf_agent.git.github_client import GithubClient
from jf_agent.git.gitlab_client import GitLabClient

from jf_agent import agent_logging, diagnostics, download_and_write_streaming, write_file

logger = logging.getLogger(__name__)

'''

    Constants

'''
BBS_PROVIDER = 'bitbucket_server'
BBC_PROVIDER = 'bitbucket_cloud'
GH_PROVIDER = 'github'
GL_PROVIDER = 'gitlab'
PROVIDERS = [GL_PROVIDER, GH_PROVIDER, BBS_PROVIDER, BBC_PROVIDER]

'''

    Normalized Structure

'''


@dataclass
class NormalizedUser:
    id: str
    name: str
    login: str
    email: str = None
    url: str = None


@dataclass
class NormalizedBranch:
    name: str
    sha: str


@dataclass
class NormalizedProject:
    id: str
    name: str
    login: str
    url: str


@dataclass
class NormalizedShortRepository:
    id: int
    name: str
    url: str


@dataclass
class NormalizedRepository:
    id: int
    name: str
    full_name: str
    url: str
    is_fork: bool
    default_branch_name: str
    project: NormalizedProject
    branches: List[NormalizedBranch]

    def short(self):
        # return the short form of Normalized Repository
        return NormalizedShortRepository(id=self.id, name=self.name, url=self.url)


@dataclass
class NormalizedCommit:
    hash: str
    url: str
    message: str
    commit_date: str
    author_date: str
    author: NormalizedUser
    repo: NormalizedShortRepository
    is_merge: bool


@dataclass
class NormalizedPullRequestComment:
    user: NormalizedUser
    body: str
    created_at: str


@dataclass
class NormalizedPullRequestReview:
    user: NormalizedUser
    foreign_id: int
    review_state: str


@dataclass
class NormalizedPullRequest:
    id: any
    additions: int
    deletions: int
    changed_files: int
    is_closed: bool
    is_merged: bool
    created_at: str
    updated_at: str
    merge_date: str
    closed_date: str
    title: str
    body: str
    url: str
    base_branch: str
    head_branch: str
    author: NormalizedUser
    merged_by: NormalizedUser
    commits: List[NormalizedCommit]
    comments: List[NormalizedPullRequestComment]
    approvals: List[NormalizedPullRequestReview]
    base_repo: NormalizedShortRepository
    head_repo: NormalizedShortRepository


class GitAdapter(ABC):
    def __init__(self, config):
        self.config = config

    @abstractmethod
    def get_users(self) -> List[NormalizedUser]:
        pass

    @abstractmethod
    def get_projects(self) -> List[NormalizedProject]:
        pass

    @abstractmethod
    def get_repos(self) -> List[NormalizedRepository]:
        pass

    @abstractmethod
    def get_default_branch_commits(
        self, api_repos, server_git_instance_info
    ) -> List[NormalizedCommit]:
        pass

    @abstractmethod
    def get_pull_requests(self, api_repos, server_git_instance_info) -> List[NormalizedPullRequest]:
        pass

    def load_and_dump_git(self, endpoint_git_instance_info):
        nrm_projects: List[NormalizedProject] = self.get_projects()
        write_file(
            self.config.outdir, 'bb_projects', self.config.compress_output_files, nrm_projects
        )

        write_file(
            self.config.outdir, 'bb_users', self.config.compress_output_files, self.get_users(),
        )
        nrm_repos = None

        @diagnostics.capture_timing()
        @agent_logging.log_entry_exit(logger)
        def get_and_write_repos():
            nonlocal nrm_repos

            nrm_repos = self.get_repos(nrm_projects,)

            write_file(self.config.outdir, 'bb_repos', self.config.compress_output_files, nrm_repos)
            return len(nrm_repos)

        get_and_write_repos()

        @diagnostics.capture_timing()
        @agent_logging.log_entry_exit(logger)
        def download_and_write_commits():
            return download_and_write_streaming(
                self.config.outdir,
                'bb_commits',
                self.config.compress_output_files,
                generator_func=self.get_default_branch_commits,
                generator_func_args=(nrm_repos, endpoint_git_instance_info,),
                item_id_dict_key='hash',
            )

        download_and_write_commits()

        @diagnostics.capture_timing()
        @agent_logging.log_entry_exit(logger)
        def download_and_write_prs():
            return download_and_write_streaming(
                self.config.outdir,
                'bb_prs',
                self.config.compress_output_files,
                generator_func=self.get_pull_requests,
                generator_func_args=(nrm_repos, endpoint_git_instance_info,),
                item_id_dict_key='id',
            )

        download_and_write_prs()


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def get_git_client(config, creds):
    try:
        if config.git_provider == BBS_PROVIDER:
            return Stash(
                base_url=config.git_url,
                username=creds.bb_server_username,
                password=creds.bb_server_password,
                verify=not config.skip_ssl_verification,
                session=retry_session(),
            )

        if config.git_provider == BBC_PROVIDER:
            return BitbucketCloudClient(
                server_base_uri=config.git_url,
                username=creds.bb_cloud_username,
                app_password=creds.bb_cloud_app_password,
                session=retry_session(),
            )

        if config.git_provider == GH_PROVIDER:
            return GithubClient(
                base_url=config.git_url,
                token=creds.github_token,
                verify=not config.skip_ssl_verification,
                session=retry_session(),
            )
        if config.git_provider == GL_PROVIDER:
            return GitLabClient(
                server_url=config.git_url,
                private_token=creds.gitlab_token,
                per_page_override=config.gitlab_per_page_override,
                session=retry_session(),
            )

    except Exception as e:
        agent_logging.log_and_print(
            logger,
            logging.ERROR,
            f'Failed to connect to {config.git_provider}:\n{e}',
            exc_info=True,
        )
        return

    # if the git provider is none of the above, throw an error
    raise ValueError(f'unsupported git provider {config.git_provider}')


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def load_and_dump_git(config, endpoint_git_instance_info, git_connection):
    try:
        if config.git_provider == 'bitbucket_server':
            # using old func method, todo: refactor to use GitAdapter
            from jf_agent.git.bitbucket_server import load_and_dump as load_and_dump_bbs

            load_and_dump_bbs(config, endpoint_git_instance_info, git_connection)
        elif config.git_provider == 'bitbucket_cloud':
            from jf_agent.git.bitbucket_cloud_adapter import BitbucketCloudAdapter

            BitbucketCloudAdapter(config, git_connection).load_and_dump_git(
                endpoint_git_instance_info
            )
        elif config.git_provider == 'github':
            # using old func method, todo: refactor to use GitAdapter
            from jf_agent.git.github import load_and_dump as load_and_dump_gh

            load_and_dump_gh(config, endpoint_git_instance_info, git_connection)
        elif config.git_provider == 'gitlab':
            from jf_agent.git.gitlab_adapter import GitLabAdapter

            GitLabAdapter(config, git_connection).load_and_dump_git(endpoint_git_instance_info)
        else:
            raise ValueError(f'unsupported git provider {config.git_provider}')

    except Exception as e:
        agent_logging.log_and_print(
            logger,
            logging.ERROR,
            f'Failed to download {config.git_provider} data:\n{e}',
            exc_info=True,
        )
        
        return {'type': 'Git', 'status': 'failed'}

    return {'type': 'Git', 'status': 'success'}


def pull_since_date_for_repo(server_git_instance_info, org_login, repo_id, commits_or_prs: str):
    assert commits_or_prs in ('commits', 'prs')

    # Only a single instance is supported / sent from the server
    instance_info = list(server_git_instance_info.values())[0]

    instance_pull_from_dt = pytz.utc.localize(datetime.fromisoformat(instance_info['pull_from']))
    instance_info_this_repo = instance_info['repos_dict'].get(f'{org_login}-{repo_id}')

    if instance_info_this_repo:
        if commits_or_prs == 'commits':
            dt_str = instance_info_this_repo['commits_backpopulated_to']
        else:
            dt_str = instance_info_this_repo['prs_backpopulated_to']
        repo_backpop_to_dt = pytz.utc.localize(datetime.fromisoformat(dt_str)) if dt_str else None
        if not repo_backpop_to_dt or instance_pull_from_dt < repo_backpop_to_dt:
            # We need to backpopulate the repo
            return instance_pull_from_dt
        else:
            if commits_or_prs == 'commits':
                # We don't need to backpopulate the repo -- pull commits for last month
                return pytz.utc.localize(datetime.utcnow() - timedelta(days=31))
            else:
                # We don't need to backpopulate the repo -- only need to pull PRs that have been updated
                # more recently than PR with the latest update_date on the already-sent PRs
                return (
                    datetime.fromisoformat(instance_info_this_repo['latest_pr_update_date_pulled'])
                    if instance_info_this_repo['latest_pr_update_date_pulled']
                    else instance_pull_from_dt
                )
    else:
        # We need to backpopulate the repo
        return instance_pull_from_dt


def get_repos_from_git(git_connection, config):
    '''
    Gets git repositories for use in the `print_apparently_missing_git_repos` run mode
    to compare git repos from git sources against git repos found by jira
    '''
    if config.git_provider == 'bitbucket_server':

        from jf_agent.git.bitbucket_server import get_projects as get_projects_bbs
        from jf_agent.git.bitbucket_server import get_repos as get_repos_bbs

        projects = get_projects_bbs(
            git_connection, config.git_include_projects, config.git_exclude_projects, False
        )
        _, repos = zip(
            *get_repos_bbs(
                git_connection, projects, config.git_include_repos, config.git_exclude_repos, False
            )
        )

    elif config.git_provider == 'bitbucket_cloud':

        from jf_agent.git.bitbucket_cloud_adapter import BitbucketCloudAdapter

        bbc_adapter = BitbucketCloudAdapter(git_connection)

        projects = bbc_adapter.get_projects(config.git_include_projects, False)
        repos = bbc_adapter.get_repos(
            projects, config.git_include_repos, config.git_exclude_repos, False
        )

    elif config.git_provider == 'github':

        from jf_agent.git.github import get_repos as get_repos_gh

        _, repos = zip(
            *get_repos_gh(
                git_connection,
                config.git_include_projects,
                config.git_include_repos,
                config.git_exclude_repos,
                False,
            )
        )

    elif config.git_provider == 'gitlab':

        from jf_agent.git.gitlab_adapter import GitLabAdapter

        gl_adapter = GitLabAdapter(git_connection)

        projects = gl_adapter.get_projects(config.git_include_projects, False)
        repos = gl_adapter.get_repos(
            projects, config.git_include_repos, config.git_exclude_repos, False
        )
    else:
        raise ValueError(f'{config.git_provider} is not a supported git_provider for this run_mode')

    return repos
