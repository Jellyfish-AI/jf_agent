import gitlab
from tqdm import tqdm
import requests
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
from jf_agent.git.gitlab_client import (
    GitLabClient,
    log_and_print_request_error,
    MissingSourceProjectException,
)
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


class GitLabAdapter(GitAdapter):
    def __init__(self, config: GitConfig, outdir: str, compress_output_files: bool, client: GitLabClient):
        super().__init__(config, outdir, compress_output_files)
        self.client = client

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_projects(self) -> List[NormalizedProject]:
        print('downloading gitlab projects... ', end='', flush=True)
        projects = [
            _normalize_project(
                self.client.get_group(project_id),
                self.config.git_redact_names_and_urls,  # are group_ids
            )
            for project_id in self.config.git_include_projects
        ]
        print('✓')

        if not projects:
            raise ValueError(
                'No projects found.  Make sure your token has appropriate access to GitLab.'
            )
        return projects

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_users(self) -> List[NormalizedUser]:
        print('downloading gitlab users... ', end='', flush=True)
        users = [
            _normalize_user(user)
            for project_id in self.config.git_include_projects
            for user in self.client.list_group_members(project_id)
        ]
        print('✓')
        return users

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_repos(
        self, normalized_projects: List[NormalizedProject],
    ) -> List[NormalizedRepository]:
        print('downloading gitlab repos... ', end='', flush=True)

        nrm_repos: List[NormalizedRepository] = []
        for nrm_project in normalized_projects:

            for i, api_repo in enumerate(
                tqdm(
                    self.client.list_group_projects(nrm_project.id),
                    desc=f'downloading repos for {nrm_project.name}',
                    unit='repos',
                ),
                start=1,
            ):
                if (
                    self.config.git_include_repos
                    and api_repo.id not in self.config.git_include_repos
                ):
                    continue  # skip this repo
                if self.config.git_exclude_repos and api_repo.id in self.config.git_exclude_repos:
                    continue  # skip this repo

                nrm_branches = self.get_branches(api_repo)
                nrm_repos.append(
                    _normalize_repo(
                        api_repo, nrm_branches, nrm_project, self.config.git_redact_names_and_urls
                    )
                )

        print('✓')
        if not nrm_repos:
            raise ValueError(
                'No repos found. Make sure your token has appropriate access to GitLab and check your configuration of repos to pull.'
            )
        return nrm_repos

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_branches(self, api_repo) -> List[NormalizedBranch]:
        print('downloading gitlab branches... ', end='', flush=True)
        try:
            return [
                _normalize_branch(api_branch, self.config.git_redact_names_and_urls)
                for api_branch in self.client.list_project_branches(api_repo.id)
            ]
        except requests.exceptions.RetryError as e:
            log_and_print_request_error(
                e,
                f'pulling branches from repo {api_repo.id}'
                'This is most likely because no repo was in the GitlabProject -- will treat like there are no branches',
            )
            return []

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_default_branch_commits(
        self, normalized_repos: List[NormalizedRepository], server_git_instance_info,
    ) -> List[NormalizedCommit]:
        print('downloading gitlab default branch commits... ', end='', flush=True)
        for i, nrm_repo in enumerate(normalized_repos, start=1):
            with agent_logging.log_loop_iters(logger, 'repo for branch commits', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, nrm_repo.project.login, nrm_repo.id, 'commits'
                )
                try:
                    for j, commit in enumerate(
                        tqdm(
                            self.client.list_project_commits(nrm_repo.id, pull_since),
                            desc=f'downloading commits for {nrm_repo.name}',
                            unit='commits',
                        ),
                        start=1,
                    ):
                        with agent_logging.log_loop_iters(
                            logger, 'branch commit inside repo', j, 100
                        ):
                            yield _normalize_commit(
                                commit,
                                nrm_repo,
                                self.config.git_strip_text_content,
                                self.config.git_redact_names_and_urls,
                            )

                except Exception as e:
                    print(
                        f':WARN: Got exception for branch {nrm_repo.default_branch_name}: {e}. Skipping...'
                    )
        print('✓')

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_pull_requests(
        self, normalized_repos: List[NormalizedRepository], server_git_instance_info,
    ) -> List[NormalizedPullRequest]:
        print('downloading gitlab prs... ', end='', flush=True)

        for i, nrm_repo in enumerate(
            tqdm(normalized_repos, desc=f'downloading prs for repos', unit='repos'), start=1
        ):

            with agent_logging.log_loop_iters(logger, 'repo for pull requests', i, 1):
                try:

                    pull_since = pull_since_date_for_repo(
                        server_git_instance_info, nrm_repo.project.login, nrm_repo.id, 'prs'
                    )

                    api_prs = self.client.list_project_merge_requests(nrm_repo.id)
                    total_api_prs = api_prs.total

                    if total_api_prs == 0:
                        print(f'no prs found for repo {nrm_repo.id}. Skipping... ')
                        logger.info(f'no prs found for repo {nrm_repo.id}. Skipping... ')
                        continue

                    for api_pr in tqdm(
                        api_prs,
                        desc=f'processing prs for {nrm_repo.name}',
                        unit='prs',
                        total=total_api_prs,
                    ):
                        try:
                            updated_at = parser.parse(api_pr.updated_at)

                            # PRs are ordered newest to oldest
                            # if this is too old, we're done with this repo
                            if pull_since and updated_at < pull_since:
                                break

                            try:
                                api_pr = self.client.expand_merge_request_data(api_pr)
                            except MissingSourceProjectException as e:
                                log_and_print_request_error(
                                    e,
                                    f'fetching source project {api_pr.source_project_id} '
                                    f'for merge_request {api_pr.id}. Skipping...',
                                )
                                continue

                            nrm_commits: List[NormalizedCommit] = [
                                _normalize_commit(
                                    commit,
                                    nrm_repo,
                                    self.config.git_strip_text_content,
                                    self.config.git_redact_names_and_urls,
                                )
                                for commit in api_pr.commit_list
                            ]

                            yield _normalize_pr(
                                api_pr,
                                nrm_commits,
                                self.config.git_strip_text_content,
                                self.config.git_redact_names_and_urls,
                            )
                        except Exception as e:
                            # if something goes wrong with normalizing one of the prs - don't stop pulling. try
                            # the next one.
                            pr_id = f' {api_pr.id}' if api_pr else ''
                            log_and_print_request_error(
                                e,
                                f'normalizing PR {pr_id} from repo {nrm_repo.id}. Skipping...',
                                log_as_exception=True,
                            )

                except Exception as e:
                    # if something happens when pulling PRs for a repo, just keep going.
                    log_and_print_request_error(
                        e, f'getting PRs for repo {nrm_repo.id}. Skipping...', log_as_exception=True
                    )

    print('✓')


'''
    
    Massage Functions
    
'''


def _normalize_user(api_user) -> NormalizedUser:
    if not api_user:
        return None

    if isinstance(api_user, dict):
        return NormalizedUser(
            id=api_user['id'],
            login=api_user['username'],
            name=api_user['name'],
            url=api_user['web_url'],
            email=None,  # no email available
        )

    return NormalizedUser(
        id=api_user.id,
        login=api_user.username,
        name=api_user.name,
        url=api_user.web_url,
        email=None,  # no email available
    )


def _normalize_project(api_group, redact_names_and_urls: bool) -> NormalizedProject:
    return NormalizedProject(
        id=api_group.id,
        login=api_group.id,
        name=api_group.name
        if not redact_names_and_urls
        else _project_redactor.redact_name(api_group.name),
        url=None,
    )


def _normalize_branch(api_branch, redact_names_and_urls: bool) -> NormalizedBranch:
    return NormalizedBranch(
        name=api_branch.name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(api_branch.name),
        sha=api_branch.commit['id'],
    )


def _normalize_repo(
    api_repo,
    normalized_branches: List[NormalizedBranch],
    normalized_project: NormalizedProject,
    redact_names_and_urls: bool,
) -> NormalizedRepository:
    repo_name = (
        api_repo.name if not redact_names_and_urls else _repo_redactor.redact_name(api_repo.name)
    )
    url = api_repo.web_url if not redact_names_and_urls else None

    return NormalizedRepository(
        id=api_repo.id,
        name=repo_name,
        full_name=repo_name,
        url=url,
        default_branch_name=_get_attribute(api_repo, 'default_branch', default=''),
        is_fork=True if _get_attribute(api_repo, 'forked_from_project') else False,
        branches=normalized_branches,
        project=normalized_project,
    )


def _normalize_short_form_repo(api_repo, redact_names_and_urls):
    return NormalizedShortRepository(
        id=api_repo.id,
        name=api_repo.name
        if not redact_names_and_urls
        else _repo_redactor.redact_name(api_repo.name),
        url=api_repo.web_url if not redact_names_and_urls else None,
    )


def _normalize_commit(
    api_commit, normalized_repo, strip_text_content: bool, redact_names_and_urls: bool
):
    author = NormalizedUser(
        id=f'{api_commit.author_name}<{api_commit.author_email}>',
        login=api_commit.author_email,
        name=api_commit.author_name,
        email=api_commit.author_email,
    )
    commit_url = (
        f'{normalized_repo.url}/commit/{api_commit.id}' if not redact_names_and_urls else None
    )
    return NormalizedCommit(
        hash=api_commit.id,
        author=author,
        url=commit_url,
        commit_date=api_commit.committed_date,
        author_date=api_commit.committed_date,
        message=sanitize_text(api_commit.message, strip_text_content),
        is_merge=len(api_commit.parent_ids) > 1,
        repo=normalized_repo.short(),  # use short form of repo
    )


def _get_normalized_pr_comments(
    merge_request, strip_text_content
) -> List[NormalizedPullRequestComment]:
    try:
        return [
            NormalizedPullRequestComment(
                user=_normalize_user(note.author),
                body=sanitize_text(note.body, strip_text_content),
                created_at=note.created_at,
            )
            for note in merge_request.note_list
        ]
    except (requests.exceptions.RetryError, gitlab.exceptions.GitlabHttpError) as e:
        log_and_print_request_error(
            e,
            f'standardizing PR comments for merge_request {merge_request.id} -- '
            f'handling it as if it has no comments',
        )
        return []


def _get_normalized_approvals(merge_request):
    try:
        return [
            NormalizedPullRequestReview(
                user=_normalize_user(approval['user']),
                foreign_id=approval['user']['id'],
                review_state='APPROVED',
            )
            for approval in merge_request.approved_by
        ]
    except (requests.exceptions.RetryError, gitlab.exceptions.GitlabHttpError) as e:
        log_and_print_request_error(
            e,
            f'standardizing PR approvals for merge_request {merge_request.id} -- '
            f'handling it as if it has no approvals',
        )
        return []


def _normalize_pr(
    merge_request,
    normalized_commits: List[NormalizedCommit],
    strip_text_content: bool,
    redact_names_and_urls: bool,
):
    base_branch_name = merge_request.source_branch
    head_branch_name = merge_request.target_branch

    # normalize comments, approvals, and commits
    additions, deletions, changed_files = _calculate_diff_counts(merge_request.diff)

    return NormalizedPullRequest(
        id=merge_request.id,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
        created_at=merge_request.created_at,
        updated_at=merge_request.updated_at,
        merge_date=merge_request.merged_at,
        closed_date=merge_request.closed_at,
        is_closed=merge_request.state == 'closed',
        is_merged=merge_request.state == 'merged',
        # redacted fields
        url=merge_request.web_url if not redact_names_and_urls else None,
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
        title=sanitize_text(merge_request.title, strip_text_content),
        body=sanitize_text(merge_request.description, strip_text_content),
        # normalized fields
        commits=normalized_commits,
        author=_normalize_user(merge_request.author),
        merged_by=_normalize_user(merge_request.merged_by),
        approvals=_get_normalized_approvals(merge_request),
        comments=_get_normalized_pr_comments(merge_request, strip_text_content),
        base_repo=_normalize_short_form_repo(merge_request.source_project, redact_names_and_urls),
        head_repo=_normalize_short_form_repo(merge_request.target_project, redact_names_and_urls),
    )


'''

    Helpers 
    
'''


def _calculate_diff_counts(diff):
    additions, deletions = 0, 0
    changed_files = None

    try:
        if diff:
            changed_files_a, changed_files_b = 0, 0
            for line in diff.splitlines():
                if line.startswith('+') and not line.startswith('+++'):
                    additions += 1
                if line.startswith('-') and not line.startswith('---'):
                    deletions += 1
                if line.startswith('--- '):
                    changed_files_a += 1
                if line.startswith('+++ '):
                    changed_files_b += 1

            if changed_files_a != changed_files_b:
                additions, deletions, changed_files = None, None, None
            else:
                changed_files = changed_files_a

    except UnicodeDecodeError:
        additions, deletions, changed_files = None, None, None

    return additions, deletions, changed_files


def _get_attribute(object, property, default=None):
    """
    Obtain a class attribute safely
    """
    try:
        value = getattr(object, property)
        return value if value else default
    except AttributeError:
        return default
