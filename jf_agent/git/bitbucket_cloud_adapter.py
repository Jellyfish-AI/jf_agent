from tqdm import tqdm
import re
from dateutil import parser
from typing import List
import logging
import requests

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
from jf_agent.git.bitbucket_cloud_client import BitbucketCloudClient

from jf_agent import diagnostics, agent_logging
from jf_agent.name_redactor import NameRedactor, sanitize_text

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()


class BitbucketCloudAdapter(GitAdapter):
    def __init__(self, config, client: BitbucketCloudClient):
        super().__init__(config)
        self.client = client

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_users(self) -> List[NormalizedUser]:
        # Bitbucket Cloud API doesn't have a way to fetch all users;
        # we'll reconstruct them from repo history (commiters, PR
        # authors, etc)
        return []

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_projects(self) -> List[NormalizedProject]:
        # Bitbucket Cloud API doesn't have a way to fetch all top-level projects;
        # instead, need to configure the agent with a specific set of projects to pull
        print('downloading bitbucket projects... ', end='', flush=True)
        projects = [
            _normalize_project(p, self.config.git_redact_names_and_urls)
            for p in self.config.git_include_projects
        ]
        print('✓')

        return projects

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_repos(
        self, normalized_projects: List[NormalizedProject],
    ) -> List[NormalizedRepository]:
        print('downloading bitbucket repos... ', end='', flush=True)

        repos = []
        for p in normalized_projects:
            for i, api_repo in enumerate(
                tqdm(
                    self.client.get_all_repos(p.id),
                    desc=f'downloading repos for {p.name}',
                    unit='repos',
                )
            ):
                # If we have an explicit repo allow list and this isn't in it, skip
                if (
                    self.config.git_include_repos
                    and api_repo['name'] not in self.config.git_include_repos
                    and api_repo['uuid'] not in self.config.git_include_repos
                ):
                    continue

                # If we have an explicit repo deny list and this is in it, skip
                if self.config.git_exclude_repos and (
                    api_repo['name'] in self.config.git_exclude_repos
                    or api_repo['uuid'] in self.config.git_exclude_repos
                ):
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
                        continue

                    # if we have a project deny list and this repo is in a project that's in it, skip
                    if self.config.git_exclude_bbcloud_projects and (
                        repo_project['key'] in self.config.git_exclude_bbcloud_projects
                        or repo_project['uuid'] in self.config.git_exclude_bbcloud_projects
                    ):
                        continue

                branches = self.get_branches(p, api_repo)
                repos.append(
                    _normalize_repo(api_repo, branches, p, self.config.git_redact_names_and_urls)
                )

        print('✓')
        if not repos:
            raise ValueError(
                'No repos found. Make sure your token has appropriate access to Bitbucket and check your configuration of repos to pull.'
            )

        return repos

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_default_branch_commits(
        self, normalized_repos: List[NormalizedRepository], server_git_instance_info,
    ) -> List[NormalizedCommit]:
        print('downloading bitbucket default branch commits... ', end='', flush=True)
        for i, repo in enumerate(normalized_repos, start=1):
            with agent_logging.log_loop_iters(logger, 'repo for branch commits', i, 1):
                pull_since = pull_since_date_for_repo(
                    server_git_instance_info, repo.project.login, repo.id, 'commits'
                )
                for j, commit in enumerate(
                    tqdm(
                        self.client.get_commits(repo.project.id, repo.id, repo.default_branch_name),
                        desc=f'downloading commits for {repo.name}',
                        unit='commits',
                    ),
                    start=1,
                ):
                    with agent_logging.log_loop_iters(logger, 'branch commit inside repo', j, 100):
                        commit = _normalize_commit(
                            commit,
                            repo,
                            self.config.git_strip_text_content,
                            self.config.git_redact_names_and_urls,
                        )
                        yield commit

                        # yield one commit older than we want to see
                        if commit.commit_date < pull_since:
                            break

        print('✓')

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_pull_requests(
        self, normalized_repos: List[NormalizedRepository], server_git_instance_info,
    ) -> List[NormalizedPullRequest]:
        print('downloading bitbucket prs... ', end='', flush=True)
        for i, repo in enumerate(
            tqdm(normalized_repos, desc=f'downloading prs for repos', unit='repos'), start=1
        ):
            with agent_logging.log_loop_iters(logger, 'repo for pull requests', i, 1):
                try:
                    pull_since = pull_since_date_for_repo(
                        server_git_instance_info, repo.project.login, repo.id, 'prs'
                    )

                    api_prs = self.client.get_pullrequests(repo.project.id, repo.id)

                    if not api_prs:
                        agent_logging.log_and_print(
                            logger, logging.INFO, f'no prs found for repo {repo.id}. Skipping... '
                        )
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
                                agent_logging.log_and_print(
                                    logger,
                                    logging.WARN,
                                    f"PR {api_pr['id']} doesn't reference a source and/or destination repository; skipping it...",
                                )
                                continue

                            yield _normalize_pr(
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
                            agent_logging.log_and_print(
                                logger,
                                logging.ERROR,
                                f'Error normalizing PR {api_pr["id"]} from repo {repo.id}. Skipping...',
                                exc_info=True,
                            )

                except Exception:
                    # if something happens when pulling PRs for a repo, just keep going.
                    agent_logging.log_and_print(
                        logger,
                        logging.ERROR,
                        f'Error getting PRs for repo {repo.id}. Skipping...',
                        exc_info=True,
                    )

        print('✓')

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_branches(self, project, api_repo) -> List[NormalizedBranch]:
        return [
            _normalize_branch(api_branch, self.config.git_redact_names_and_urls)
            for api_branch in self.client.get_branches(project.id, api_repo['uuid'])
        ]


def _normalize_project(project_name, redact_names_and_urls):
    return NormalizedProject(id=project_name, login=project_name, name=project_name, url=None)


def _normalize_repo(
    api_repo,
    normalized_branches: List[NormalizedBranch],
    normalized_project: NormalizedProject,
    redact_names_and_urls: bool,
) -> NormalizedRepository:
    repo_name = (
        api_repo['name']
        if not redact_names_and_urls
        else _repo_redactor.redact_name(api_repo['name'])
    )
    url = api_repo['links']['self']['href'] if not redact_names_and_urls else None

    return NormalizedRepository(
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
        branches=normalized_branches,
        project=normalized_project,
    )


def _normalize_short_form_repo(api_repo, redact_names_and_urls):
    return NormalizedShortRepository(
        id=api_repo['uuid'],
        name=(
            api_repo['name']
            if not redact_names_and_urls
            else _repo_redactor.redact_name(api_repo['name'])
        ),
        url=api_repo['links']['self']['href'] if not redact_names_and_urls else None,
    )


def _normalize_branch(api_branch, redact_names_and_urls: bool) -> NormalizedBranch:
    return NormalizedBranch(
        name=(
            api_branch['name']
            if not redact_names_and_urls
            else _branch_redactor.redact_name(api_branch['name'])
        ),
        sha=api_branch['target']['hash'],
    )


def _normalize_commit(
    api_commit, normalized_repo, strip_text_content: bool, redact_names_and_urls: bool
):
    author = _normalize_user(api_commit['author'])
    commit_url = api_commit['links']['html']['href'] if not redact_names_and_urls else None
    return NormalizedCommit(
        hash=api_commit['hash'],
        author=author,
        url=commit_url,
        commit_date=parser.parse(api_commit['date']),
        author_date=None,  # Not available in BB Cloud API,
        message=sanitize_text(api_commit['message'], strip_text_content),
        is_merge=len(api_commit['parents']) > 1,
        repo=normalized_repo.short(),  # use short form of repo
    )


def _normalize_user(api_user):
    if 'uuid' in api_user:
        # This is a bitbucket cloud web user, we know their UUID

        return NormalizedUser(
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
        return NormalizedUser(id=raw_name, login=email, name=username, email=email,)

    return NormalizedUser(id=raw_name, name=raw_name, login=raw_name,)


def _normalize_pr(
    client, repo, api_pr, strip_text_content: bool, redact_names_and_urls: bool,
):

    # Process the PR's diff to get additions, deletions, changed_files
    additions, deletions, changed_files = None, None, None
    try:
        diff_str = client.pr_diff(repo.project.id, repo.id, api_pr['id'])
        additions, deletions, changed_files = _calculate_diff_counts(diff_str)
        if additions is None:
            agent_logging.log_and_print(
                logger,
                logging.WARN,
                f'Unable to parse the diff For PR {api_pr["id"]} in repo {repo.id}; proceeding as though no files were changed.',
            )

    except requests.exceptions.HTTPError as e:
        if e.response.status_code >= 500:
            # Server threw a 500 on the request for the diff; this happens consistently for certain PRs
            # (if the PR has no commits yet). Just proceed with no diff
            pass
        elif e.response.status_code == 401:
            # Server threw a 401 on the request for the diff; not sure why this would be, but it seems rare
            agent_logging.log_and_print(
                logger,
                logging.WARN,
                f'For PR {api_pr["id"]} in repo {repo.id}, caught HTTPError (HTTP 401) when attempting to retrieve changes; '
                f'proceeding as though no files were changed',
            )
        else:
            # Some other HTTP error happened; Re-raise
            raise
    except UnicodeDecodeError:
        # Occasional diffs seem to be invalid UTF-8
        agent_logging.log_and_print(
            logger,
            logging.WARN,
            f'For PR {api_pr["id"]} in repo {repo.id}, caught UnicodeDecodeError when attempting to decode changes; '
            f'proceeding as though no files were changed',
        )

    # Comments
    comments = [
        NormalizedPullRequestComment(
            user=_normalize_user(c['user']),
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
            NormalizedPullRequestReview(
                user=_normalize_user(approval['user']),
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
                merged_by = _normalize_user(a['update']['author'])
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
        _normalize_commit(c, repo, strip_text_content, redact_names_and_urls)
        for c in client.pr_commits(repo.project.id, repo.id, api_pr['id'])
    ]

    # Repo links
    base_repo = _normalize_short_form_repo(
        api_pr['destination']['repository'], redact_names_and_urls
    )
    head_repo = _normalize_short_form_repo(api_pr['source']['repository'], redact_names_and_urls)

    return NormalizedPullRequest(
        id=api_pr['id'],
        title=api_pr['title'],
        body=api_pr['description'],
        url=api_pr['links']['html']['href'],
        base_branch=api_pr['destination']['branch']['name'],
        head_branch=api_pr['source']['branch']['name'],
        base_repo=base_repo,
        head_repo=head_repo,
        author=_normalize_user(api_pr['author']),
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
