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
    NormalizedPullRequestRepository
)
from jf_agent import pull_since_date_for_repo, agent_logging
from jf_agent import diagnostics
from jf_agent.name_redactor import NameRedactor, sanitize_text

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()

'''
    
    Data Fetching

'''


class GitLabAdapter(GitAdapter):

    def __init__(self, client: gitlab.Gitlab):
        self.client = client

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_users(self, include_group_ids) -> List[NormalizedUser]:
        print('downloading gitlab users... ', end='', flush=True)
        users = [
            _normalize_user(user)
            for group_id in include_group_ids
            for user in self.self.client.list_group_members(group_id)
        ]
        print('✓')
        return users

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_projects(self, include_groups, redact_names_and_urls) -> List[NormalizedProject]:
        print('downloading gitlab projects... ', end='', flush=True)
        projects = [
            _normalize_project(group, redact_names_and_urls)
            for group in include_groups
        ]
        print('✓')

        if not projects:
            raise ValueError(
                'No projects found.  Make sure your token has appropriate access to GitLab.'
            )
        return projects

    @agent_logging.log_entry_exit(logger)
    def get_repos(
            self, include_groups_ids, include_repos, exclude_repos, redact_names_and_urls
    ) -> List[NormalizedRepository]:
        print('downloading gitlab repos... ', end='', flush=True)

        filters = []
        if include_repos:
            filters.append(lambda r: r.name in include_repos)
        if exclude_repos:
            filters.append(lambda r: r.name not in exclude_repos)

        repos = [
            (repo, _normalize_repo(self, repo, group, redact_names_and_urls))
            for group_id in include_groups
            for repo in self.client.list_group_projects(group)  # gitlab project == repo
            if all(filt(repo) for filt in filters)
        ]
        print('✓')
        if not repos:
            raise ValueError(
                'No repos found. Make sure your token has appropriate access to GitLab and check your configuration of repos to pull.'
            )
        return repos

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_default_branch_commits(
            self, api_repos, strip_text_content, server_git_instance_info, redact_names_and_urls
    ) -> List[NormalizedCommit]:
        for i, api_repo in enumerate(api_repos, start=1):
            with agent_logging.log_loop_iters(logger, 'repo for branch commits', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, api_repo.group['id'], api_repo.id, 'commits'
                )
                try:
                    for j, commit in enumerate(
                            tqdm(
                                self.client.list_project_commits(api_repo.id, pull_since),
                                desc=f'downloading commits for {api_repo.name}',
                                unit='commits',
                            ),
                            start=1,
                    ):
                        with agent_logging.log_loop_iters(logger, 'branch commit inside repo', j, 100):
                            yield _normalize_commit(
                                commit, api_repo, strip_text_content, redact_names_and_urls
                            )

                except Exception as e:
                    print(f':WARN: Got exception for branch {api_repo.default_branch}: {e}. Skipping...')

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_pull_requests(
            self, api_repos, strip_text_content, server_git_instance_info, redact_names_and_urls
    ) -> List[NormalizedPullRequest]:
        for i, api_repo in enumerate(api_repos, start=1):
            with agent_logging.log_loop_iters(logger, 'repo for pull requests', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, api_repo.group['id'], api_repo.id, 'commits'
                )
                try:
                    for j, api_pr in enumerate(
                            tqdm(
                                self.client.list_project_merge_requests(api_repo.id),
                                desc=f'downloading PRs for {api_repo.name}',
                                unit='prs',
                            ),
                            start=1,
                    ):
                        with agent_logging.log_loop_iters(logger, 'pr inside repo', j, 10):
                            updated_at = parser.parse(api_pr.updated_at)

                            # PRs are ordered newest to oldest
                            # if this is too old, we're done with this repo
                            if pull_since and updated_at < pull_since:
                                break

                            yield _normalize_pr(self, api_pr, strip_text_content, redact_names_and_urls)

                except Exception as e:
                    print(f':WARN: Exception getting PRs for repo {api_repo.name}: {e}. Skipping...')
        print()

        return


'''
    
    Massage Functions
    
'''


def _normalize_user(api_user) -> NormalizedUser:
    if not api_user:
        return None

    return NormalizedUser(
        id=api_user.id,
        login=api_user.username,
        name=api_user.name,
        email=None
    )


def _normalize_project(api_group, redact_names_and_urls: bool) -> NormalizedProject:
    return NormalizedProject(
        id=api_group.id,
        login=api_group.id,
        name=api_group.name if not redact_names_and_urls else _project_redactor.redact_name(api_group.name),
        url=None  # todo, is this availiable?
    )


def _normalize_branch(api_branch, redact_names_and_urls: bool) -> NormalizedBranch:
    return NormalizedBranch(
        name=api_branch.name if not redact_names_and_urls else _branch_redactor.redact_name(api_branch.name),
        sha=api_branch.commit['id']
    )


def _normalize_repo(self, api_repo, api_group, redact_names_and_urls: bool) -> NormalizedRepository:
    repo_name = api_repo.name if not redact_names_and_urls else _repo_redactor.redact_name(api_repo.name)
    url = api_repo.web_url if not redact_names_and_urls else None

    branches: List[NormalizedBranch] = [
        _normalize_branch(branch, redact_names_and_urls)
        for branch in self.client.get_branches()
    ]
    project: NormalizedProject = _normalize_project(api_group, redact_names_and_urls)

    return NormalizedRepository(
        id=api_repo.id,
        name=repo_name,
        full_name=repo_name,
        url=url,
        default_branch_name=_get_attribute(api_repo, 'default_branch', default=''),
        is_fork=True if _get_attribute(api_repo, 'forked_from_project') else False,
        branches=branches,
        project=project,
    )


def _normalize_commit(commit, repo, strip_text_content: bool, redact_names_and_urls: bool):
    author = NormalizedUser(
        id=f'{commit.author_name}<{commit.author_email}>',
        login=commit.author_email,
        name=commit.author_name,
        email=commit.author_email,
    )
    commit_url = f'{repo.url}/commit/{commit.id}' if not redact_names_and_urls else None
    return NormalizedCommit(
        hash=commit.id,
        author=author,
        url=commit_url,
        commit_date=commit.committed_date,
        author_date=commit.committed_date,
        message=sanitize_text(commit.message, strip_text_content),
        is_merge=len(commit.parent_ids) > 1,
    )


def _normalize_pr_repo(api_repo, redact_names_and_urls):
    return NormalizedPullRequestRepository(
        id=api_repo.id,
        name=api_repo.name if not redact_names_and_urls else _repo_redactor.redact_name(api_repo.name),
        url=api_repo.web_url if not redact_names_and_urls else None
    )


def _get_normalized_pr_comments(merge_request, strip_text_content) -> List[NormalizedPullRequestComment]:
    try:
        return [
            NormalizedPullRequestComment(
                user=_normalize_user(note.author),
                body=sanitize_text(note.body, strip_text_content),
                created_at=note.created_at
            )
            for note in merge_request.note_list
        ]
    except (requests.exceptions.RetryError, gitlab.exceptions.GitlabHttpError) as e:
        logger.warning(
            f'Got {type(e)} ({e}) when standardizing PR comments for merge_request {merge_request.id} -- '
            f'handling it as if it has no comments'
        )
        return []


def _get_normalized_approvals(merge_request):
    try:
        return [
            NormalizedPullRequestReview(user=_normalize_user(approver), foreign_id=approver.user_id,
                                        review_state='approved')
            for approver in merge_request.approved_by
        ]
    except (requests.exceptions.RetryError, gitlab.exceptions.GitlabHttpError) as e:
        logger.warning(
            f'Got {type(e)} ({e}) when standardizing PR approvals for merge_request {merge_request.id} -- '
            f'handling it as if it has no approvals'
        )
        return []


def _normalize_pr(self, merge_request, strip_text_content, redact_names_and_urls):
    merge_request = self.client.expand_merge_request_data(merge_request)

    base_branch_name = merge_request.source_branch['name']
    head_branch_name = merge_request.target_branch['name']

    # normalize comments, approvals, and commits
    commits = [_normalize_commit(commit) for commit in merge_request.commit_list]
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
        base_branch=base_branch_name if not redact_names_and_urls else _branch_redactor.redact_name(base_branch_name),
        head_branch=head_branch_name if not redact_names_and_urls else _branch_redactor.redact_name(head_branch_name),
        # sanitized fields
        title=sanitize_text(merge_request.title, strip_text_content),
        body=sanitize_text(merge_request.description, strip_text_content),
        # normalized fields
        commits=commits,
        author=_normalize_user(merge_request.author),
        merged_by=_normalize_user(merge_request.merged_by),
        approvals=_get_normalized_approvals(merge_request),
        comments=_get_normalized_pr_comments(merge_request, strip_text_content),
        base_repo=_normalize_pr_repo(merge_request.source_project, redact_names_and_urls),
        head_repo=_normalize_pr_repo(merge_request.target_project, redact_names_and_urls),
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
