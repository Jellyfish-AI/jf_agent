import traceback
from jf_agent.git.github_gql_client import GithubGqlClient
from jf_agent.git.utils import get_branches_for_normalized_repo
from tqdm import tqdm
from dateutil import parser
from typing import List
import logging
from jf_agent.git import (
    GitAdapter,
    NormalizedUser,
    NormalizedProject,
    NormalizedBranch,
    NormalizedRepository,
    NormalizedCommit,
    NormalizedPullRequest,
    NormalizedPullRequestComment,
    NormalizedPullRequestReview,
    NormalizedShortRepository,
    pull_since_date_for_repo,
)
from jf_agent.git.utils import log_and_print_request_error
from jf_agent import diagnostics, agent_logging
from jf_agent.name_redactor import NameRedactor, sanitize_text
from jf_agent.config_file_reader import GitConfig

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()

'''

    Data Fetching

'''


class GithubGqlAdapter(GitAdapter):
    def __init__(
        self, config: GitConfig, outdir: str, compress_output_files: bool, client: GithubGqlClient
    ):
        super().__init__(config, outdir, compress_output_files)
        self.client = client

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_projects(self) -> List[NormalizedProject]:
        print('downloading github projects... ', end='', flush=True)
        projects = []

        # NOTE: For github, project equates to a Github organization here!
        for project_id in self.config.git_include_projects:
            organization = self.client.get_organization_by_login(project_id)

            if organization is None:  # skip organizations that errored out when fetching data
                continue

            projects.append(
                _normalize_project(organization, self.config.git_redact_names_and_urls,)
            )
        print('✓')

        if not projects:
            raise ValueError(
                'No projects found.  Make sure your token has appropriate access to Github.'
            )
        return projects

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_users(self) -> List[NormalizedUser]:
        print('downloading github users... ', end='', flush=True)
        users = [
            _normalize_user(user)
            for project_id in self.config.git_include_projects
            for user in self.client.get_users(project_id)
        ]
        print('✓')
        return users

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_repos(
        self, normalized_projects: List[NormalizedProject],
    ) -> List[NormalizedRepository]:
        print('downloading github repos... ', end='', flush=True)

        nrm_repos: List[NormalizedRepository] = []

        filters = []
        if self.config.git_include_repos:
            filters.append(
                lambda r: r['name'].lower()
                in set([r.lower() for r in self.config.git_include_repos])
            )
        if self.config.git_exclude_repos:
            filters.append(
                lambda r: r['name'].lower()
                not in set([r.lower() for r in self.config.git_exclude_repos])
            )

        nrm_repos = [
            _normalize_repo(api_repo, nrm_project, self.config.git_redact_names_and_urls)
            for nrm_project in normalized_projects
            for api_repo in tqdm(
                self.client.get_repos(nrm_project.login, repo_filters=filters),
                desc=f'downloading repos for {nrm_project.login}',
                unit='repos',
            )
        ]

        print('✓')
        if not nrm_repos:
            raise ValueError(
                'No repos found. Make sure your token has appropriate access to Github and check your configuration of repos to pull.'
            )
        return nrm_repos

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_commits_for_included_branches(
        self,
        normalized_repos: List[NormalizedRepository],
        included_branches: dict,
        server_git_instance_info,
    ) -> List[NormalizedCommit]:
        print('downloading github commits on included branches... ', end='', flush=True)
        for i, nrm_repo in enumerate(normalized_repos, start=1):
            with agent_logging.log_loop_iters(logger, 'repo for branch commits', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, nrm_repo.project.login, nrm_repo.id, 'commits'
                )

                try:
                    for branch_name in get_branches_for_normalized_repo(
                        nrm_repo, included_branches
                    ):
                        for j, api_commit in enumerate(
                            tqdm(
                                self.client.get_commits(
                                    login=nrm_repo.project.login,
                                    repo_name=nrm_repo.name,
                                    branch_name=branch_name,
                                    since=pull_since,
                                ),
                                desc=f'downloading commits for branch {branch_name} in repo {nrm_repo.name} ({nrm_repo.project.login})',
                                unit='commits',
                            ),
                            start=1,
                        ):
                            with agent_logging.log_loop_iters(
                                logger, 'branch commit inside repo', j, 100
                            ):
                                yield _normalize_commit(
                                    api_commit,
                                    nrm_repo,
                                    branch_name,
                                    self.config.git_strip_text_content,
                                    self.config.git_redact_names_and_urls,
                                )

                except Exception as e:
                    print(traceback.format_exc())
                    print(f':WARN: Got exception for branch {branch_name}: {e}. Skipping...')
        print('✓')

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_pull_requests(
        self, normalized_repos: List[NormalizedRepository], server_git_instance_info,
    ) -> List[NormalizedPullRequest]:
        print('downloading github prs... ', end='', flush=True)

        nrm_prs = []
        for i, nrm_repo in enumerate(normalized_repos, start=1):
            print(f'downloading prs for repo {nrm_repo.name} ({nrm_repo.id})')

            with agent_logging.log_loop_iters(logger, 'repo for pull requests', i, 1):
                try:
                    pull_since = pull_since_date_for_repo(
                        server_git_instance_info, nrm_repo.project.login, nrm_repo.id, 'prs'
                    )

                    # This will return a page size between 0 and 25 (upper bound set by GithubGqlClient.MAX_PAGE_SIZE_FOR_PR_QUERY)
                    def _get_pagesize_for_prs() -> int:
                        api_prs_updated_at_only = self.client.get_pr_last_update_dates(
                            login=nrm_repo.project.login,
                            repo_name=nrm_repo.name,
                            page_size=GithubGqlClient.MAX_PAGE_SIZE_FOR_PR_QUERY,
                        )
                        updated_at_values = [
                            1
                            for api_pr in api_prs_updated_at_only
                            if pull_since and parser.parse(api_pr['updatedAt']) >= pull_since
                        ]
                        return len(updated_at_values)

                    page_size = _get_pagesize_for_prs()

                    api_prs = self.client.get_prs(
                        login=nrm_repo.project.login, repo_name=nrm_repo.name, page_size=page_size
                    )

                    for api_pr in tqdm(
                        api_prs,
                        desc=f'processing prs for {nrm_repo.name} ({nrm_repo.id})',
                        unit='prs',
                    ):
                        try:
                            updated_at = parser.parse(api_pr['updatedAt'])

                            # PRs are ordered newest to oldest
                            # if this is too old, we're done with this repo
                            if pull_since and updated_at < pull_since:
                                break

                            nrm_prs.append(
                                _normalize_pr(
                                    api_pr,
                                    nrm_repo,
                                    self.config.git_strip_text_content,
                                    self.config.git_redact_names_and_urls,
                                )
                            )
                        except Exception as e:
                            # if something goes wrong with normalizing one of the prs - don't stop pulling. try
                            # the next one.
                            pr_id = f' {api_pr["id"]}' if api_pr else ''
                            log_and_print_request_error(
                                e,
                                f'normalizing PR {pr_id} from repo {nrm_repo.name} ({nrm_repo.id}). Skipping...',
                                log_as_exception=True,
                            )

                except Exception as e:
                    # if something happens when pulling PRs for a repo, just keep going.
                    log_and_print_request_error(
                        e,
                        f'getting PRs for repo {nrm_repo.name} ({nrm_repo.id}). Skipping...',
                        log_as_exception=True,
                    )
        return nrm_prs

    print('✓')


'''

    Massage Functions

'''


def _normalize_user(api_user) -> NormalizedUser:
    if not api_user:
        return None

    # raw user, just have email (e.g. from a commit)
    if 'id' not in api_user:
        return NormalizedUser(
            id=api_user['email'],
            login=api_user['email'],
            name=api_user['name'],
            email=api_user['email'],
        )

    # API user, where github matched to a known account
    return NormalizedUser(
        id=api_user['id'], login=api_user['login'], name=api_user['name'], email=api_user['email']
    )


def _normalize_project(api_org: dict, redact_names_and_urls: bool) -> NormalizedProject:
    return NormalizedProject(
        id=api_org['id'],
        login=api_org['login'],
        name=api_org['name']
        if not redact_names_and_urls
        else _project_redactor.redact_name(api_org['name']),
        url=api_org['url'] if not redact_names_and_urls else None,
    )


def _normalize_branch(api_branch, redact_names_and_urls: bool) -> NormalizedBranch:
    return NormalizedBranch(
        name=api_branch['name']
        if not redact_names_and_urls
        else _branch_redactor.redact_name(api_branch['name']),
        sha=api_branch['target']['sha'],
    )


def _normalize_repo(
    api_repo, normalized_project: NormalizedProject, redact_names_and_urls: bool,
) -> NormalizedRepository:
    repo_name = (
        api_repo['name']
        if not redact_names_and_urls
        else _repo_redactor.redact_name(api_repo['name'])
    )
    url = api_repo['url'] if not redact_names_and_urls else None

    normalized_branches = [
        _normalize_branch(branch, redact_names_and_urls) for branch in api_repo['branches']
    ]

    return NormalizedRepository(
        id=api_repo['id'],
        name=repo_name,
        full_name=repo_name,
        url=url,
        default_branch_name=api_repo['defaultBranch']['name'],
        is_fork=api_repo['isFork'],
        branches=normalized_branches,
        project=normalized_project,
    )


def _normalize_short_form_repo(
    api_repo: dict, redact_names_and_urls: dict
) -> NormalizedShortRepository:
    repo_name = (
        api_repo['name']
        if not redact_names_and_urls
        else _repo_redactor.redact_name(api_repo['name'])
    )
    url = api_repo['url'] if not redact_names_and_urls else None

    return NormalizedShortRepository(id=api_repo['id'], name=repo_name, url=url)


def _normalize_commit(
    api_commit: dict,
    normalized_repo: NormalizedRepository,
    branch_name: str,
    strip_text_content: bool,
    redact_names_and_urls: bool,
):
    author = _normalize_user(api_commit['author'])
    commit_url = api_commit['url'] if not redact_names_and_urls else None
    return NormalizedCommit(
        hash=api_commit['sha'],
        author=author,
        url=commit_url,
        commit_date=api_commit['committedDate'],
        author_date=api_commit['authoredDate'],
        message=sanitize_text(api_commit['message'], strip_text_content),
        is_merge=api_commit['parents']['totalCount'] > 1,
        repo=normalized_repo.short(),  # use short form of repo
        branch_name=branch_name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(branch_name),
    )


def _get_normalized_pr_comments(
    api_comments: list[dict], strip_text_content
) -> List[NormalizedPullRequestComment]:
    return [
        NormalizedPullRequestComment(
            user=_normalize_user(api_comment['author']),
            body=sanitize_text(api_comment['body'], strip_text_content),
            created_at=api_comment['createdAt'],
        )
        for api_comment in api_comments
    ]


def _get_normalized_reviews(api_reviews: list[dict]):
    return [
        NormalizedPullRequestReview(
            user=_normalize_user(api_review['author']),
            foreign_id=api_review['id'],
            review_state=api_review['state'],
        )
        for api_review in api_reviews
    ]


def _normalize_pr(
    api_pr,
    normalized_repo: NormalizedRepository,
    strip_text_content: bool,
    redact_names_and_urls: bool,
):
    base_branch_name = api_pr['baseRef']['name'] if api_pr['baseRef'] else None
    head_branch_name = api_pr['headRef']['name'] if api_pr['headRef'] else None
    normalized_merge_commit = (
        _normalize_commit(
            api_pr['mergeCommit'],
            normalized_repo=normalized_repo,
            branch_name=base_branch_name,
            strip_text_content=strip_text_content,
            redact_names_and_urls=redact_names_and_urls,
        )
        if api_pr['mergeCommit']
        else None
    )
    return NormalizedPullRequest(
        id=api_pr['id'],
        additions=api_pr['additions'],
        deletions=api_pr['deletions'],
        changed_files=api_pr['changedFiles'],
        created_at=api_pr['createdAt'],
        updated_at=api_pr['updatedAt'],
        merge_date=api_pr['mergedAt'],
        closed_date=api_pr['closedAt'],
        is_closed=api_pr['state'].lower() == 'closed',
        is_merged=api_pr['merged'],
        # redacted fields
        url=api_pr['url'] if not redact_names_and_urls else None,
        base_branch=(
            base_branch_name
            if not redact_names_and_urls
            else _branch_redactor.redact_name(base_branch_name)
        ),
        head_branch=(
            head_branch_name
            if not redact_names_and_urls
            else _branch_redactor.redact_name(head_branch_name)
        ),
        # sanitized fields
        title=sanitize_text(api_pr['title'], strip_text_content),
        body=sanitize_text(api_pr['body'], strip_text_content),
        # normalized fields
        commits=[
            _normalize_commit(
                api_commit=commit,
                normalized_repo=normalized_repo,
                branch_name=base_branch_name,
                strip_text_content=strip_text_content,
                redact_names_and_urls=redact_names_and_urls,
            )
            for commit in api_pr['commits']
        ],
        merge_commit=normalized_merge_commit,
        author=_normalize_user(api_user=api_pr['author']),
        merged_by=_normalize_user(api_user=api_pr['mergedBy']),
        approvals=_get_normalized_reviews(api_pr['reviews']),
        comments=_get_normalized_pr_comments(api_pr['comments'], strip_text_content),
        base_repo=_normalize_short_form_repo(api_pr['baseRepository'], redact_names_and_urls),
        head_repo=_normalize_short_form_repo(api_pr['baseRepository'], redact_names_and_urls),
    )
