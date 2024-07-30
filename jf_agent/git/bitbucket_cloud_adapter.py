from jf_agent.git.utils import get_branches_for_standardized_repo
from tqdm import tqdm
import re
from dateutil import parser
from typing import List
import logging
import requests

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
from jf_agent.git.bitbucket_cloud_client import BitbucketCloudClient

from jf_agent.name_redactor import NameRedactor, sanitize_text
from jf_agent.config_file_reader import GitConfig
from jf_ingest import diagnostics, logging_helper

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()


class BitbucketCloudAdapter(GitAdapter):
    def __init__(
        self,
        config: GitConfig,
        outdir: str,
        compress_output_files: bool,
        client: BitbucketCloudClient,
    ):
        super().__init__(config, outdir, compress_output_files)
        self.client = client

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_users(self) -> List[StandardizedUser]:
        # Bitbucket Cloud API doesn't have a way to fetch all users;
        # we'll reconstruct them from repo history (commiters, PR
        # authors, etc)
        return []

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_projects(self) -> List[StandardizedProject]:
        # Bitbucket Cloud API doesn't have a way to fetch all top-level projects;
        # instead, need to configure the agent with a specific set of projects to pull
        logger.info('downloading bitbucket projects... [!n]')
        projects = [
            _standardize_project(p, self.config.git_redact_names_and_urls)
            for p in self.config.git_include_projects
        ]
        logger.info('✓')

        return projects

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_repos(
        self, standardized_projects: List[StandardizedProject],
    ) -> List[StandardizedRepository]:
        logger.info('downloading bitbucket repos...')

        repos = []
        for p in standardized_projects:
            for i, api_repo in enumerate(
                tqdm(
                    self.client.get_all_repos(p.id),
                    desc=f'downloading repos for {p.name}',
                    unit='repos',
                )
            ):
                # If we have an explicit repo allow list and this isn't in it, skip
                if self.config.git_include_repos:
                    git_include_repos_lowered = set(
                        n.lower() for n in self.config.git_include_repos
                    )
                    if (
                        api_repo['name'].lower() not in git_include_repos_lowered
                        and api_repo['uuid'].lower() not in git_include_repos_lowered
                    ):
                        if self.config.git_verbose:
                            logger.info(
                                f'''Skipping repo "{api_repo['name']}" ({api_repo['uuid']}) because it's not in git_include_repos''',
                            )
                        continue

                # If we have an explicit repo deny list and this is in it, skip
                if self.config.git_exclude_repos:
                    git_exclude_repos_lowered = set(
                        n.lower() for n in self.config.git_exclude_repos
                    )
                    if (
                        api_repo['name'].lower() in git_exclude_repos_lowered
                        or api_repo['uuid'].lower() in git_exclude_repos_lowered
                    ):
                        if self.config.git_verbose:
                            logger.info(
                                f'''Skipping repo "{api_repo['name']}" ({api_repo['uuid']}) because it's in git_exclude_repos''',
                            )
                        continue

                # If this repo is in a project, apply project filters:
                repo_project = api_repo.get('project')
                if repo_project:
                    # If we have a project allow list and this repo is in a project that's not in it, skip
                    if (
                        self.config.git_include_bbcloud_projects
                        and repo_project['key'] not in self.config.git_include_bbcloud_projects
                        and repo_project['uuid'] not in self.config.git_include_bbcloud_projects
                    ):
                        if self.config.git_verbose:
                            logger.info(
                                f'''Skipping repo "{api_repo['name']}" ({api_repo['uuid']}) because its project '''
                                f'''("{repo_project['key']}"/{repo_project['uuid']}) is not in git_include_bbcloud_projects''',
                            )
                        continue

                    # if we have a project deny list and this repo is in a project that's in it, skip
                    if self.config.git_exclude_bbcloud_projects and (
                        repo_project['key'] in self.config.git_exclude_bbcloud_projects
                        or repo_project['uuid'] in self.config.git_exclude_bbcloud_projects
                    ):
                        if self.config.git_verbose:
                            logger.info(
                                f'''Skipping repo "{api_repo['name']}" ({api_repo['uuid']}) because its project '''
                                f'''("{repo_project['key']}"/{repo_project['uuid']}) is in git_exclude_bbcloud_projects''',
                            )
                        continue

                branches = self.get_branches(p, api_repo)
                repos.append(
                    _standardize_repo(api_repo, branches, p, self.config.git_redact_names_and_urls)
                )

        logger.info('Done downloading repos!')
        if not repos:
            raise ValueError(
                'No repos found. Make sure your token has appropriate access to Bitbucket and check your configuration of repos to pull.'
            )

        return repos

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_commits_for_included_branches(
        self,
        standardized_repos: List[StandardizedRepository],
        included_branches: dict,
        server_git_instance_info,
    ) -> List[StandardizedCommit]:
        logger.info('downloading bitbucket commits on included branches...')
        for i, repo in enumerate(standardized_repos, start=1):
            with logging_helper.log_loop_iters('repo for branch commits', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, repo.project.login, repo.id, 'commits'
                )

                for branch in get_branches_for_standardized_repo(repo, included_branches):
                    for j, commit in enumerate(
                        tqdm(
                            self.client.get_commits(repo.project.id, repo.id, branch),
                            desc=f'downloading commits for {repo.name} on branch {branch}',
                            unit='commits',
                        ),
                        start=1,
                    ):
                        with logging_helper.log_loop_iters('branch commit inside repo', j, 100):
                            commit = _standardize_commit(
                                commit,
                                repo,
                                branch,
                                self.config.git_strip_text_content,
                                self.config.git_redact_names_and_urls,
                            )
                            yield commit

                            # yield one commit older than we want to see
                            if commit.commit_date < pull_since:
                                break

        logger.info('Done downloading commits on included branches!')

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_pull_requests(
        self, standardized_repos: List[StandardizedRepository], server_git_instance_info,
    ) -> List[StandardizedPullRequest]:
        logger.info('downloading bitbucket prs...')
        for i, repo in enumerate(
            tqdm(standardized_repos, desc='downloading prs for repos', unit='repos'), start=1
        ):
            with logging_helper.log_loop_iters('repo for pull requests', i, 1):
                try:
                    pull_since = pull_since_date_for_repo(
                        server_git_instance_info, repo.project.login, repo.id, 'prs'
                    )

                    api_prs = self.client.get_pullrequests(repo.project.id, repo.id)

                    if not api_prs:
                        logger.info(f'no prs found for repo {repo.id}. Skipping... ')
                        continue

                    for api_pr in tqdm(api_prs, desc=f'processing prs for {repo.name}', unit='prs'):
                        try:
                            # Skip PRs with missng data
                            if (
                                'source' not in api_pr
                                or 'repository' not in api_pr['source']
                                or not api_pr['source']['repository']
                                or 'destination' not in api_pr
                                or 'repository' not in api_pr['destination']
                                or not api_pr['destination']['repository']
                            ):
                                logging_helper.log_standard_error(
                                    logging.WARNING, msg_args=[api_pr['id']], error_code=3030,
                                )
                                continue

                            yield _standardize_pr(
                                self.client,
                                repo,
                                api_pr,
                                self.config.git_strip_text_content,
                                self.config.git_redact_names_and_urls,
                            )

                            # PRs are ordered newest to oldest if this
                            # is too old, we're done with this repo.  We
                            # yield one old one on purpose so that we
                            # handle the case correctly when the most
                            # recent PR is really old.
                            if pull_since and parser.parse(api_pr['updated_on']) < pull_since:
                                break

                        except Exception:
                            # if something happens when normalizing a PR, just keep going with the rest
                            logging_helper.log_standard_error(
                                logging.ERROR,
                                msg_args=[api_pr["id"], repo.id],
                                error_code=3011,
                                exc_info=True,
                            )

                except Exception:
                    # if something happens when pulling PRs for a repo, just keep going.
                    logging_helper.log_standard_error(
                        logging.ERROR, msg_args=[repo.id], error_code=3021, exc_info=True,
                    )

        logger.info('Done downloading PRs!')

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_branches(self, project, api_repo) -> List[StandardizedBranch]:
        return [
            _standardize_branch(api_branch, self.config.git_redact_names_and_urls)
            for api_branch in self.client.get_branches(project.id, api_repo['uuid'])
        ]


def _standardize_project(project_name, redact_names_and_urls):
    return StandardizedProject(id=project_name, login=project_name, name=project_name, url=None)


def _standardize_repo(
    api_repo,
    standardized_branches: List[StandardizedBranch],
    standardized_project: StandardizedProject,
    redact_names_and_urls: bool,
) -> StandardizedRepository:
    repo_name = (
        api_repo['name']
        if not redact_names_and_urls
        else _repo_redactor.redact_name(api_repo['name'])
    )
    url = api_repo['links']['self']['href'] if not redact_names_and_urls else None

    return StandardizedRepository(
        id=api_repo['uuid'],
        name=repo_name,
        full_name=api_repo['full_name'],
        url=url,
        default_branch_name=(
            api_repo['mainbranch']['name']
            if 'mainbranch' in api_repo and api_repo['mainbranch']
            else None
        ),
        is_fork='origin' in api_repo,
        branches=standardized_branches,
        project=standardized_project,
    )


def _standardize_short_form_repo(api_repo, redact_names_and_urls):
    return StandardizedShortRepository(
        id=api_repo['uuid'],
        name=(
            api_repo['name']
            if not redact_names_and_urls
            else _repo_redactor.redact_name(api_repo['name'])
        ),
        url=api_repo['links']['self']['href'] if not redact_names_and_urls else None,
    )


def _standardize_branch(api_branch, redact_names_and_urls: bool) -> StandardizedBranch:
    return StandardizedBranch(
        name=(
            api_branch['name']
            if not redact_names_and_urls
            else _branch_redactor.redact_name(api_branch['name'])
        ),
        sha=api_branch['target']['hash'],
    )


def _standardize_commit(
    api_commit,
    standardized_repo,
    branch_name,
    strip_text_content: bool,
    redact_names_and_urls: bool,
):
    author = _standardize_user(api_commit['author'])
    commit_url = api_commit['links']['html']['href'] if not redact_names_and_urls else None
    return StandardizedCommit(
        hash=api_commit['hash'],
        author=author,
        url=commit_url,
        commit_date=parser.parse(api_commit['date']),
        author_date=None,  # Not available in BB Cloud API,
        message=sanitize_text(api_commit['message'], strip_text_content),
        is_merge=len(api_commit['parents']) > 1,
        repo=standardized_repo.short(),  # use short form of repo
        branch_name=branch_name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(branch_name),
    )


def _standardize_user(api_user):
    if 'uuid' in api_user:
        # This is a bitbucket cloud web user, we know their UUID

        return StandardizedUser(
            id=api_user['uuid'],
            name=api_user['display_name'],
            login=api_user.get('username'),
            url=api_user['links']['html']['href'],
            account_id=api_user.get('account_id'),
            # no email available
        )

    # This is a raw commit from a git client that didn't match a
    # known bitbucket web user.  Parse out name and email.
    raw_name = api_user.get('raw')
    m = re.search(r'^(.*)<(.*)>$', raw_name)
    if m:
        username, email = m.groups()
        username = username.strip()
        email = email.strip()
        return StandardizedUser(id=raw_name, login=email, name=username, email=email,)

    return StandardizedUser(id=raw_name, name=raw_name, login=raw_name,)


def _standardize_pr(
    client, repo, api_pr, strip_text_content: bool, redact_names_and_urls: bool,
):
    # Process the PR's diff to get additions, deletions, changed_files
    additions, deletions, changed_files = None, None, None
    try:
        diff_str = client.pr_diff(repo.project.id, repo.id, api_pr['id'])
        additions, deletions, changed_files = _calculate_diff_counts(diff_str)
        if additions is None:
            logging_helper.log_standard_error(
                logging.WARNING, msg_args=[api_pr["id"], repo.id], error_code=3031,
            )
    except requests.exceptions.RetryError:
        # Server threw a 500 on the request for the diff and we started retrying;
        # this happens consistently for certain PRs (if the PR has no commits yet). Just proceed with no diff
        pass
    except requests.exceptions.HTTPError as e:
        if e.response.status_code >= 500:
            # Server threw a 500 on the request for the diff; this happens consistently for certain PRs
            # (if the PR has no commits yet). Just proceed with no diff
            pass
        elif e.response.status_code == 401:
            # Server threw a 401 on the request for the diff; not sure why this would be, but it seems rare
            logging_helper.log_standard_error(
                logging.WARNING, msg_args=[api_pr["id"], repo.id], error_code=3041,
            )
        else:
            # Some other HTTP error happened; Re-raise
            raise
    except UnicodeDecodeError:
        # Occasional diffs seem to be invalid UTF-8
        logging_helper.log_standard_error(
            logging.WARNING, msg_args=[api_pr["id"], repo.id], error_code=3051,
        )

    # Comments
    comments = [
        StandardizedPullRequestComment(
            user=_standardize_user(c['user']),
            body=sanitize_text(c['content']['raw'], strip_text_content),
            created_at=parser.parse(c['created_on']),
        )
        for c in client.pr_comments(repo.project.id, repo.id, api_pr['id'])
    ]

    # Crawl activity for approvals, merge and closed dates
    approvals = []
    merge_date = None
    merged_by = None
    closed_date = None
    try:
        activity = list(client.pr_activity(repo.project.id, repo.id, api_pr['id']))
        approvals = [
            StandardizedPullRequestReview(
                user=_standardize_user(approval['user']),
                foreign_id=i,  # There's no true ID (unlike with GitHub); use a per-PR sequence
                review_state='APPROVED',
            )
            for i, approval in enumerate(
                (a['approval'] for a in activity if 'approval' in a), start=1,
            )
        ]

        # Obtain the merge_date and merged_by by crawling over the activity history
        pr_updates = [a for a in activity if 'update' in a]
        for a in sorted(pr_updates, key=lambda x: x['update']['date'], reverse=True):
            if a['update']['state'] == 'MERGED':
                merge_date = parser.parse(a['update']['date'])
                merged_by = _standardize_user(a['update']['author'])
                break

        # Obtain the closed_date by crawling over the activity history, looking for the
        # first transition to one of the closed states ('MERGED' or 'DECLINED')
        for a in sorted(pr_updates, key=lambda x: x['update']['date'], reverse=False):
            if a['update']['state'] in ('MERGED', 'DECLINED'):
                closed_date = parser.parse(a['update']['date'])
                break
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            # not authorized to see activity; skip it
            pass
        else:
            raise

    # Commits
    commits = [
        _standardize_commit(
            c,
            repo,
            api_pr['destination']['branch']['name'],
            strip_text_content,
            redact_names_and_urls,
        )
        for c in client.pr_commits(repo.project.id, repo.id, api_pr['id'])
    ]
    merge_commit = None
    if (
        api_pr['state'] == 'MERGED'
        and 'merge_commit' in api_pr
        and api_pr['merge_commit']
        and api_pr['merge_commit'].get('hash')
    ):
        api_merge_commit = client.get_commit(
            repo.project.id, api_pr['source']['repository']['uuid'], api_pr['merge_commit']['hash']
        )
        merge_commit = _standardize_commit(
            api_merge_commit,
            repo,
            api_pr['destination']['branch']['name'],
            strip_text_content,
            redact_names_and_urls,
        )

    # Repo links
    base_repo = _standardize_short_form_repo(
        api_pr['destination']['repository'], redact_names_and_urls
    )
    head_repo = _standardize_short_form_repo(api_pr['source']['repository'], redact_names_and_urls)

    return StandardizedPullRequest(
        id=api_pr['id'],
        title=api_pr['title'],
        body=api_pr['description'],
        url=api_pr['links']['html']['href'],
        base_branch=api_pr['destination']['branch']['name'],
        head_branch=api_pr['source']['branch']['name'],
        base_repo=base_repo,
        head_repo=head_repo,
        author=_standardize_user(api_pr['author']),
        is_closed=api_pr['state'] != 'OPEN',
        is_merged=api_pr['state'] == 'MERGED',
        created_at=parser.parse(api_pr['created_on']),
        updated_at=parser.parse(api_pr['updated_on']),
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
        merge_date=merge_date,
        closed_date=closed_date,
        merged_by=merged_by,
        approvals=approvals,
        commits=commits,
        merge_commit=merge_commit,
        comments=comments,
    )


def _calculate_diff_counts(diff):
    """
    Process a PR's diff to get the total count of additions, deletions, changed_files
    :param diff: string
    :return: int:additions, int:deletions, int:changed_files
    """
    additions, deletions = 0, 0
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

        if changed_files_a == changed_files_b:
            return additions, deletions, changed_files_a

    return None, None, None
