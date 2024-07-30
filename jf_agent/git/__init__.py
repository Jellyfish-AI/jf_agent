import pytz
import os

from typing import List
from dataclasses import dataclass
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import logging
from jf_agent.git.github_gql_client import GithubGqlClient
from stashy.client import Stash

from jf_agent.git.utils import BBC_PROVIDER, BBS_PROVIDER, GH_PROVIDER, GL_PROVIDER
from jf_agent.session import retry_session
from jf_agent.git.bitbucket_cloud_client import BitbucketCloudClient
from jf_agent.git.github_client import GithubClient
from jf_agent.git.gitlab_client import GitLabClient
from jf_agent.config_file_reader import GitConfig

from jf_agent import download_and_write_streaming, write_file
from jf_ingest import logging_helper, diagnostics
from jf_ingest.config import IngestionConfig
from jf_ingest.jf_git.adapters import GitAdapter as JFIngestGitAdapter

logger = logging.getLogger(__name__)

'''

    Standardized Structure

'''


@dataclass
class StandardizedUser:
    id: str
    name: str
    login: str
    email: str = None
    url: str = None
    account_id: str = None


@dataclass
class StandardizedBranch:
    name: str
    sha: str


@dataclass
class StandardizedProject:
    id: str
    name: str
    login: str
    url: str


@dataclass
class StandardizedShortRepository:
    id: int
    name: str
    url: str


@dataclass
class StandardizedRepository:
    id: int
    name: str
    full_name: str
    url: str
    is_fork: bool
    default_branch_name: str
    project: StandardizedProject
    branches: List[StandardizedBranch]

    def short(self):
        # return the short form of Standardized Repository
        return StandardizedShortRepository(id=self.id, name=self.name, url=self.url)


@dataclass
class StandardizedCommit:
    hash: str
    url: str
    message: str
    commit_date: str
    author_date: str
    author: StandardizedUser
    repo: StandardizedShortRepository
    is_merge: bool
    branch_name: str = None


@dataclass
class StandardizedPullRequestComment:
    user: StandardizedUser
    body: str
    created_at: str
    system_generated: bool = None


@dataclass
class StandardizedPullRequestReview:
    user: StandardizedUser
    foreign_id: int
    review_state: str


@dataclass
class StandardizedPullRequest:
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
    author: StandardizedUser
    merged_by: StandardizedUser
    commits: List[StandardizedCommit]
    merge_commit: StandardizedCommit
    comments: List[StandardizedPullRequestComment]
    approvals: List[StandardizedPullRequestReview]
    base_repo: StandardizedShortRepository
    head_repo: StandardizedShortRepository


class GitAdapter(ABC):
    def __init__(self, config: GitConfig, outdir: str, compress_output_files: bool):
        self.config = config
        self.outdir = outdir
        self.compress_output_files = compress_output_files

    @abstractmethod
    def get_users(self) -> List[StandardizedUser]:
        pass

    @abstractmethod
    def get_projects(self) -> List[StandardizedProject]:
        pass

    @abstractmethod
    def get_repos(self) -> List[StandardizedRepository]:
        pass

    @abstractmethod
    def get_commits_for_included_branches(
        self, api_repos, server_git_instance_info
    ) -> List[StandardizedCommit]:
        pass

    @abstractmethod
    def get_pull_requests(
        self, api_repos, server_git_instance_info
    ) -> List[StandardizedPullRequest]:
        pass

    def load_and_dump_git(self, endpoint_git_instance_info):
        nrm_projects: List[StandardizedProject] = self.get_projects()
        write_file(self.outdir, 'bb_projects', self.compress_output_files, nrm_projects)

        write_file(
            self.outdir, 'bb_users', self.compress_output_files, self.get_users(),
        )
        nrm_repos = None

        @diagnostics.capture_timing()
        @logging_helper.log_entry_exit(logger)
        def get_and_write_repos():
            nonlocal nrm_repos

            nrm_repos = self.get_repos(nrm_projects,)

            write_file(self.outdir, 'bb_repos', self.compress_output_files, nrm_repos)
            return len(nrm_repos)

        get_and_write_repos()

        @diagnostics.capture_timing()
        @logging_helper.log_entry_exit(logger)
        def download_and_write_commits():
            return download_and_write_streaming(
                self.outdir,
                'bb_commits',
                self.compress_output_files,
                generator_func=self.get_commits_for_included_branches,
                generator_func_args=(
                    nrm_repos,
                    self.config.git_include_branches,
                    endpoint_git_instance_info,
                ),
                item_id_dict_key='hash',
            )

        download_and_write_commits()

        @diagnostics.capture_timing()
        @logging_helper.log_entry_exit(logger)
        def download_and_write_prs():
            return download_and_write_streaming(
                self.outdir,
                'bb_prs',
                self.compress_output_files,
                generator_func=self.get_pull_requests,
                generator_func_args=(nrm_repos, endpoint_git_instance_info,),
                item_id_dict_key='id',
            )

        download_and_write_prs()


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def get_git_client(
    config: GitConfig, git_creds: dict, skip_ssl_verification: bool, instance_info: dict = {}
):
    try:
        if config.git_provider == BBS_PROVIDER:
            return Stash(
                base_url=config.git_url,
                username=git_creds['bb_server_username'],
                password=git_creds['bb_server_password'],
                verify=not skip_ssl_verification,
                session=retry_session(),
            )

        if config.git_provider == BBC_PROVIDER:
            return BitbucketCloudClient(
                server_base_uri=config.git_url,
                username=git_creds['bb_cloud_username'],
                app_password=git_creds['bb_cloud_app_password'],
                session=retry_session(),
            )

        if config.git_provider == GH_PROVIDER:
            if instance_info.get('supports_graphql_endpoints', False):
                return GithubGqlClient(
                    base_url=config.git_url,
                    token=git_creds['github_token'],
                    verify=not skip_ssl_verification,
                    session=retry_session(),
                )
            else:
                return GithubClient(
                    base_url=config.git_url,
                    token=git_creds['github_token'],
                    verify=not skip_ssl_verification,
                    session=retry_session(),
                )
        if config.git_provider == GL_PROVIDER:
            return GitLabClient(
                server_url=config.git_url,
                private_token=git_creds['gitlab_token'],
                verify=not skip_ssl_verification,
                per_page_override=config.gitlab_per_page_override,
                session=retry_session(),
            )

    except Exception as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[config.git_provider, e], error_code=2101, exc_info=True,
        )
        return

    # if the git provider is none of the above, throw an error
    raise ValueError(f'unsupported git provider {config.git_provider}')


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def load_and_dump_git(
    config: GitConfig,
    endpoint_git_instance_info: dict,
    outdir: str,
    compress_output_files: bool,
    git_connection,
    jf_options: dict,
    jf_ingest_config: IngestionConfig,
):
    # use the unique git instance agent key to collate files
    instance_slug = endpoint_git_instance_info['slug']
    instance_key = endpoint_git_instance_info['key']
    outdir = f'{outdir}/git_{instance_key}'
    os.mkdir(outdir)
    try:
        if config.git_provider == 'bitbucket_server':
            # using old func method, todo: refactor to use GitAdapter
            from jf_agent.git.bitbucket_server import load_and_dump as load_and_dump_bbs

            load_and_dump_bbs(
                config=config,
                outdir=outdir,
                compress_output_files=compress_output_files,
                endpoint_git_instance_info=endpoint_git_instance_info,
                bb_conn=git_connection,
            )

        elif config.git_provider == 'bitbucket_cloud':
            from jf_agent.git.bitbucket_cloud_adapter import BitbucketCloudAdapter

            BitbucketCloudAdapter(
                config, outdir, compress_output_files, git_connection
            ).load_and_dump_git(endpoint_git_instance_info,)
        elif config.git_provider == 'github':
            if endpoint_git_instance_info.get('supports_graphql_endpoints', False):
                for jf_ingest_git_config in jf_ingest_config.git_configs:
                    if jf_ingest_git_config.instance_slug == instance_slug:
                        logger.info(
                            f'Setting up JF Ingest GQL adapter for instance {jf_ingest_git_config.instance_slug}'
                        )
                        git_adapter: JFIngestGitAdapter = JFIngestGitAdapter.get_git_adapter(
                            jf_ingest_git_config
                        )
                        git_adapter.load_and_dump_git(
                            git_config=jf_ingest_git_config, ingest_config=jf_ingest_config
                        )
            else:
                # using old func method, todo: refactor to use GitAdapter
                # NOTE: We can hopefully do this with the above githubGqlAdapter!
                from jf_agent.git.github import load_and_dump as load_and_dump_gh

                load_and_dump_gh(
                    config=config,
                    outdir=outdir,
                    compress_output_files=compress_output_files,
                    endpoint_git_instance_info=endpoint_git_instance_info,
                    git_conn=git_connection,
                )
        elif config.git_provider == 'gitlab':
            from jf_agent.git.gitlab_adapter import GitLabAdapter

            GitLabAdapter(config, outdir, compress_output_files, git_connection).load_and_dump_git(
                endpoint_git_instance_info
            )
        else:
            raise ValueError(f'unsupported git provider {config.git_provider}')

    except Exception as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[config.git_provider, e], error_code=3061, exc_info=True,
        )

        return {
            'type': 'Git',
            'instance': instance_slug,
            'instance_key': instance_key,
            'status': 'failed',
        }

    return {
        'type': 'Git',
        'instance': instance_slug,
        'instance_key': instance_key,
        'status': 'success',
    }


def pull_since_date_for_repo(instance_info, org_login, repo_id, commits_or_prs: str):
    assert commits_or_prs in ('commits', 'prs')

    instance_pull_from_dt = pytz.utc.localize(datetime.fromisoformat(instance_info['pull_from']))
    instance_info_this_repo = instance_info['repos_dict_v2'].get(str(repo_id))

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


def get_repos_from_git(git_connection, config: GitConfig):
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

        projects = bbc_adapter.get_projects()
        repos = bbc_adapter.get_repos(projects)

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

        gl_adapter = GitLabAdapter(
            config=config, outdir='', compress_output_files=False, client=git_connection
        )

        projects = gl_adapter.get_projects()
        repos = gl_adapter.get_repos(projects)
    else:
        raise ValueError(f'{config.git_provider} is not a supported git_provider for this run_mode')
    return repos


def get_nested_repos_from_git(git_connection, config: GitConfig):
    """
    Gets repositories from git, nested in their respective projects.
    Does a lot of code borrowing from "get_projects" / "get_repos" in order to
    get the repos that belong to a project.

    @return: Dict: str -> List[str]
    """
    output_dict = {}

    if config.git_provider == 'bitbucket_server':
        from jf_agent.git.bitbucket_server import get_projects as get_projects_bbs

        projects = get_projects_bbs(
            git_connection, config.git_include_projects, config.git_exclude_projects, False
        )

        filters = []
        if config.git_include_repos:
            filters.append(
                lambda r: r['name'].lower() in set([r.lower() for r in config.git_include_repos])
            )
        if config.git_exclude_repos:
            filters.append(
                lambda r: r['name'].lower()
                not in set([r.lower() for r in config.git_exclude_repos])
            )

        for api_project, _standardized_project_dict in projects:
            project_repos = []
            project = git_connection.projects[api_project['key']]
            for repo in project.repos.list():
                if all(filt(repo) for filt in filters):
                    project_repos.append(repo['name'])
            output_dict[api_project.get('name')] = project_repos

    elif config.git_provider == 'bitbucket_cloud':
        from jf_agent.git.bitbucket_cloud_adapter import BitbucketCloudAdapter

        bbc_adapter = BitbucketCloudAdapter(
            config=config, outdir='', compress_output_files=False, client=git_connection
        )

        projects = bbc_adapter.get_projects()
        for project in projects:
            project_repos = [x.name for x in bbc_adapter.get_repos([project])]
            output_dict[project.name] = project_repos

    elif config.git_provider == 'github':
        from jf_agent.git.github import _standardize_repo

        filters = []
        if config.git_include_repos:
            filters.append(
                lambda r: r['name'].lower() in set([r.lower() for r in config.git_include_repos])
            )
        if config.git_exclude_repos:
            filters.append(
                lambda r: r['name'].lower()
                not in set([r.lower() for r in config.git_exclude_repos])
            )

        for org in config.git_include_projects:
            org_repos = [
                _standardize_repo(git_connection, org, r, config.git_redact_names_and_urls)
                for r in git_connection.get_all_repos(org)
                if all(filt(r) for filt in filters)
            ]
            output_dict[org] = [x.name for x in org_repos]

    elif config.git_provider == 'gitlab':
        from jf_agent.git.gitlab_adapter import GitLabAdapter

        gl_adapter = GitLabAdapter(
            config=config, outdir='', compress_output_files=False, client=git_connection
        )

        projects = gl_adapter.get_projects()
        for project in projects:
            # For GitLab, the config file specifies repo IDs not names -- so that's what we want to validate with
            # project_repos = [x.name for x in gl_adapter.get_repos([project])]
            project_repos = [x.id for x in gl_adapter.get_repos([project])]
            output_dict[project.name] = project_repos

    else:
        raise ValueError(f'{config.git_provider} is not a supported git_provider for this run_mode')

    return output_dict
