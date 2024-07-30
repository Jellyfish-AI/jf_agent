from jf_agent.git.utils import get_branches_for_standardized_repo
import gitlab
from tqdm import tqdm
import requests
from dateutil import parser
from typing import List
from gitlab.v4.objects.projects import GroupProject
import logging
from jf_agent.git import (
    GitAdapter,
    StandardizedUser,
    StandardizedProject,
    StandardizedBranch,
    StandardizedRepository,
    StandardizedCommit,
    StandardizedPullRequest,
    StandardizedPullRequestComment,
    StandardizedPullRequestReview,
    StandardizedShortRepository,
    pull_since_date_for_repo,
)
from jf_agent.git.gitlab_client import (
    GitLabClient,
    MissingSourceProjectException,
)
from jf_agent.git.utils import log_and_print_request_error
from jf_agent.name_redactor import NameRedactor, sanitize_text
from jf_agent.config_file_reader import GitConfig
from jf_ingest import diagnostics, logging_helper

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()

'''

    Data Fetching

'''


class GitLabAdapter(GitAdapter):
    def __init__(
        self, config: GitConfig, outdir: str, compress_output_files: bool, client: GitLabClient
    ):
        super().__init__(config, outdir, compress_output_files)
        self.client = client

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_projects(self) -> List[StandardizedProject]:
        logger.info('downloading gitlab projects... [!n]')
        projects = []

        for project_id in self.config.git_include_projects:
            group = self.client.get_group(project_id)

            if group is None:  # skip groups that errored out when fetching data
                continue

            projects.append(
                _standardize_project(group, self.config.git_redact_names_and_urls,)  # are group_ids
            )
        logger.info('✓')

        if not projects:
            raise ValueError(
                'No projects found.  Make sure your token has appropriate access to GitLab.'
            )
        return projects

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_users(self) -> List[StandardizedUser]:
        logger.info('downloading gitlab users... [!n]')
        users = [
            _standardize_user(user)
            for project_id in self.config.git_include_projects
            for user in self.client.list_group_members(project_id)
        ]
        logger.info('✓')
        return users

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_repos(
        self, standardized_projects: List[StandardizedProject],
    ) -> List[StandardizedRepository]:
        logger.info('downloading gitlab repos...')

        nrm_repos: List[StandardizedRepository] = []
        for nrm_project in standardized_projects:
            repos_that_failed_to_download = []

            for i, api_repo in enumerate(
                tqdm(
                    self.client.list_group_projects(nrm_project.id),
                    desc=f'downloading repos for {nrm_project.name}',
                    unit='repos',
                ),
                start=1,
            ):
                if not _should_fetch_repo_data(api_repo, self.config):
                    continue

                try:
                    nrm_branches = self.get_branches(api_repo)
                except gitlab.exceptions.GitlabListError:
                    # this is likely due to fine-tuned permissions defined on the repo (gitlab project)
                    # that is not allowing us to access to its repo details. if this happens, make a note of it and
                    # don't blow up the rest of the pull
                    repos_that_failed_to_download.append(api_repo)
                    continue  # skip this repo

                nrm_repos.append(
                    _standardize_repo(
                        api_repo, nrm_branches, nrm_project, self.config.git_redact_names_and_urls
                    )
                )

            # if there were any repositories we had issues with... print them out now.
            if repos_that_failed_to_download:

                def __repo_log_string(api_repo):
                    # build log string
                    name = (
                        api_repo.name
                        if not self.config.git_redact_names_and_urls
                        else _repo_redactor.redact_name(api_repo.name)
                    )
                    return {"id": api_repo.id, "name": name}.__str__()

                repos_failed_string = ", ".join(
                    [__repo_log_string(api_repo) for api_repo in repos_that_failed_to_download]
                )
                total_failed = len(repos_that_failed_to_download)

                logging_helper.log_standard_error(
                    logging.WARNING,
                    msg_args=[total_failed, nrm_project.id, repos_failed_string],
                    error_code=2201,
                )

        logger.info('Done downloading gitlab repos!')
        if not nrm_repos:
            raise ValueError(
                'No repos found. Make sure your token has appropriate access to GitLab and check your configuration of repos to pull.'
            )
        return nrm_repos

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_branches(self, api_repo) -> List[StandardizedBranch]:
        logger.info('downloading gitlab branches... [!n]')
        try:
            branches = [
                _standardize_branch(api_branch, self.config.git_redact_names_and_urls)
                for api_branch in self.client.list_project_branches(api_repo.id)
            ]
        except requests.exceptions.RetryError as e:
            log_and_print_request_error(
                e,
                f'pulling branches from repo {api_repo.id}'
                'This is most likely because no repo was in the GitlabProject -- will treat like there are no branches',
            )
            branches = []
        logger.info('✓')
        return branches

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_commits_for_included_branches(
        self,
        standardized_repos: List[StandardizedRepository],
        included_branches: dict,
        server_git_instance_info,
    ) -> List[StandardizedCommit]:
        logger.info('downloading gitlab commits on included branches...')
        for i, nrm_repo in enumerate(standardized_repos, start=1):
            with logging_helper.log_loop_iters('repo for branch commits', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, nrm_repo.project.login, nrm_repo.id, 'commits'
                )

                try:
                    for branch in get_branches_for_standardized_repo(nrm_repo, included_branches):
                        for j, commit in enumerate(
                            tqdm(
                                self.client.list_project_commits(nrm_repo.id, pull_since, branch),
                                desc=f'downloading commits for branch {branch} in repo {nrm_repo.name} ({nrm_repo.id})',
                                unit='commits',
                            ),
                            start=1,
                        ):
                            with logging_helper.log_loop_iters('branch commit inside repo', j, 100):
                                yield _standardize_commit(
                                    commit,
                                    nrm_repo,
                                    branch,
                                    self.config.git_strip_text_content,
                                    self.config.git_redact_names_and_urls,
                                )

                except Exception as e:
                    logger.info(f':WARN: Got exception for branch {branch}: {e}. Skipping...')
        logger.info('Done downloading gitlab commits on included branches!')

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_pull_requests(
        self, standardized_repos: List[StandardizedRepository], server_git_instance_info,
    ) -> List[StandardizedPullRequest]:
        logger.info('downloading gitlab prs...')

        for i, nrm_repo in enumerate(standardized_repos, start=1):
            logger.info(f'downloading prs for repo {nrm_repo.name} ({nrm_repo.id})')

            with logging_helper.log_loop_iters('repo for pull requests', i, 1):
                try:
                    pull_since = pull_since_date_for_repo(
                        server_git_instance_info, nrm_repo.project.login, nrm_repo.id, 'prs'
                    )

                    api_prs = self.client.list_project_merge_requests(nrm_repo.id)

                    for api_pr in tqdm(
                        api_prs,
                        desc=f'processing prs for {nrm_repo.name} ({nrm_repo.id})',
                        unit='prs',
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

                            nrm_commits: List[StandardizedCommit] = [
                                _standardize_commit(
                                    commit,
                                    nrm_repo,
                                    api_pr.target_branch,
                                    self.config.git_strip_text_content,
                                    self.config.git_redact_names_and_urls,
                                )
                                for commit in api_pr.commit_list
                            ]
                            merge_request = self.client.expand_merge_request_data(api_pr)
                            merge_commit = None
                            if (
                                merge_request.state == 'merged'
                                and nrm_commits is not None
                                and merge_request.merge_commit_sha
                            ):
                                merge_commit = _standardize_commit(
                                    self.client.get_project_commit(
                                        merge_request.project_id, merge_request.merge_commit_sha
                                    ),
                                    nrm_repo,
                                    api_pr.target_branch,
                                    self.config.git_strip_text_content,
                                    self.config.git_redact_names_and_urls,
                                )

                            yield _standardize_pr(
                                api_pr,
                                nrm_commits,
                                self.config.git_strip_text_content,
                                self.config.git_redact_names_and_urls,
                                merge_commit,
                            )
                        except Exception as e:
                            # if something goes wrong with normalizing one of the prs - don't stop pulling. try
                            # the next one.
                            pr_id = f' {api_pr.id}' if api_pr else ''
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

    logger.info('Done downloading gitlab PRs!')


'''

    Massage Functions

'''


def _standardize_user(api_user) -> StandardizedUser:
    if not api_user:
        return None

    if isinstance(api_user, dict):
        return StandardizedUser(
            id=api_user['id'],
            login=api_user['username'],
            name=api_user['name'],
            url=api_user['web_url'],
            email=None,  # no email available
        )

    return StandardizedUser(
        id=api_user.id,
        login=api_user.username,
        name=api_user.name,
        url=api_user.web_url,
        email=None,  # no email available
    )


def _standardize_project(api_group, redact_names_and_urls: bool) -> StandardizedProject:
    return StandardizedProject(
        id=api_group.id,
        login=api_group.id,
        name=api_group.name
        if not redact_names_and_urls
        else _project_redactor.redact_name(api_group.name),
        url=None,
    )


def _standardize_branch(api_branch, redact_names_and_urls: bool) -> StandardizedBranch:
    return StandardizedBranch(
        name=api_branch.name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(api_branch.name),
        sha=api_branch.commit['id'],
    )


def _standardize_repo(
    api_repo,
    standardized_branches: List[StandardizedBranch],
    standardized_project: StandardizedProject,
    redact_names_and_urls: bool,
) -> StandardizedRepository:
    repo_name = (
        api_repo.name if not redact_names_and_urls else _repo_redactor.redact_name(api_repo.name)
    )
    url = api_repo.web_url if not redact_names_and_urls else None

    return StandardizedRepository(
        id=api_repo.id,
        name=repo_name,
        full_name=repo_name,
        url=url,
        default_branch_name=_get_attribute(api_repo, 'default_branch', default=''),
        is_fork=True if _get_attribute(api_repo, 'forked_from_project') else False,
        branches=standardized_branches,
        project=standardized_project,
    )


def _standardize_short_form_repo(api_repo, redact_names_and_urls):
    return StandardizedShortRepository(
        id=api_repo.id,
        name=api_repo.name
        if not redact_names_and_urls
        else _repo_redactor.redact_name(api_repo.name),
        url=api_repo.web_url if not redact_names_and_urls else None,
    )


def _standardize_commit(
    api_commit,
    standardized_repo,
    branch_name,
    strip_text_content: bool,
    redact_names_and_urls: bool,
):
    author = StandardizedUser(
        id=f'{api_commit.author_name}<{api_commit.author_email}>',
        login=api_commit.author_email,
        name=api_commit.author_name,
        email=api_commit.author_email,
    )
    commit_url = (
        f'{standardized_repo.url}/commit/{api_commit.id}' if not redact_names_and_urls else None
    )
    return StandardizedCommit(
        hash=api_commit.id,
        author=author,
        url=commit_url,
        commit_date=api_commit.committed_date,
        author_date=api_commit.authored_date,
        message=sanitize_text(api_commit.message, strip_text_content),
        is_merge=len(api_commit.parent_ids) > 1,
        repo=standardized_repo.short(),  # use short form of repo
        branch_name=branch_name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(branch_name),
    )


def _get_standardized_pr_comments(
    merge_request, strip_text_content
) -> List[StandardizedPullRequestComment]:
    try:
        return [
            StandardizedPullRequestComment(
                user=_standardize_user(note.author),
                body=sanitize_text(note.body, strip_text_content),
                created_at=note.created_at,
                system_generated=note.system,
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


def _get_standardized_approvals(merge_request):
    try:
        return [
            StandardizedPullRequestReview(
                user=_standardize_user(approval['user']),
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


def _standardize_pr(
    merge_request,
    standardized_commits: List[StandardizedCommit],
    strip_text_content: bool,
    redact_names_and_urls: bool,
    merge_commit,
):
    base_branch_name = merge_request.target_branch
    head_branch_name = merge_request.source_branch

    # standardize comments, approvals, and commits
    additions, deletions, changed_files = _calculate_diff_counts(merge_request.diff)

    # OJ-7701: GitLab merge requests have a PATCH in the diff attribute, not standard diff format. We can't
    # determine the number of files changed from a patch, but we can get the number of lines added and deleted.
    # To get the number of files changed, we can just use the length of the list returned from changes(), which
    # contains file information for each file changed in the merge request.
    changed_files = len(merge_request.changes()['changes'])

    return StandardizedPullRequest(
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
        # standardized fields
        commits=standardized_commits,
        merge_commit=merge_commit,
        author=_standardize_user(merge_request.author),
        merged_by=_standardize_user(merge_request.merged_by),
        approvals=_get_standardized_approvals(merge_request),
        comments=_get_standardized_pr_comments(merge_request, strip_text_content),
        base_repo=_standardize_short_form_repo(merge_request.target_project, redact_names_and_urls),
        head_repo=_standardize_short_form_repo(merge_request.source_project, redact_names_and_urls),
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


def _should_fetch_repo_data(api_repo: GroupProject, config: GitConfig) -> bool:
    """
    Determines whether a certain repo's data should be fetched.

    For GitLab, git_include_repos holds IDs instead of names (probably unintentionally), so
    no need to be case insensitive
    """
    include_rules_defined = bool(
        config.git_include_repos or config.git_include_all_repos_inside_projects
    )
    exclude_rules_defined = bool(
        config.git_exclude_repos or config.git_exclude_all_repos_inside_projects
    )

    if not (include_rules_defined or exclude_rules_defined):
        # Always pull from a repo if there are no special rules
        return True

    # In GitLab's terminology, a group ID; in Jellyfish's terminology, a project ID
    api_repo_parent_project_id = api_repo.namespace["id"]

    included_explicitly = bool(config.git_include_repos) and api_repo.id in config.git_include_repos
    included_implicitly = (
        bool(config.git_include_all_repos_inside_projects)
        and api_repo_parent_project_id in config.git_include_all_repos_inside_projects
    )

    included = included_explicitly or included_implicitly

    excluded_explicitly = bool(config.git_exclude_repos) and api_repo.id in config.git_exclude_repos
    excluded_implicitly = (
        bool(config.git_exclude_all_repos_inside_projects)
        and api_repo_parent_project_id in config.git_exclude_all_repos_inside_projects
    )

    excluded = excluded_explicitly or excluded_implicitly

    if include_rules_defined and not included:
        if config.git_verbose:
            logger.info(
                f"skipping repo {api_repo.id} because it is not included explicitly or implicitly, "
                f"while include rules are defined...",
            )
        return False

    if exclude_rules_defined and excluded:
        if config.git_verbose:
            logger.info(
                f'skipping repo {api_repo.id} because it is excluded explicitly or implicitly,'
                f'while exclude rules are defined...',
            )
        return False

    return True
