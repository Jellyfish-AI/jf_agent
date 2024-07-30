import traceback
from dateutil import parser
import logging
from tqdm import tqdm

from jf_agent.git import (
    GithubClient,
    StandardizedBranch,
    StandardizedCommit,
    StandardizedProject,
    StandardizedPullRequest,
    StandardizedPullRequestComment,
    StandardizedPullRequestReview,
    StandardizedRepository,
    StandardizedShortRepository,
    StandardizedUser,
)
from jf_agent.git import pull_since_date_for_repo
from jf_agent.git.utils import get_matching_branches
from jf_agent.name_redactor import NameRedactor, sanitize_text
from jf_agent import download_and_write_streaming, write_file
from jf_agent.config_file_reader import GitConfig
from jf_ingest import diagnostics, logging_helper

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def load_and_dump(
    config: GitConfig,
    outdir: str,
    compress_output_files: bool,
    endpoint_git_instance_info: dict,
    git_conn: GithubClient,
):
    # Logic to help with debugging the scopes that clients have with their API key
    try:
        scopes = git_conn.get_scopes_of_api_token()
        logging_helper.send_to_agent_log_file(
            'Attempting to ingest github data with the following '
            f'scopes for {config.git_instance_slug}: {scopes}',
        )
    except Exception as e:
        logging_helper.send_to_agent_log_file(
            f'Problem finding scopes for your API key. Error: {e}', level=logging.ERROR
        )
        logging_helper.send_to_agent_log_file(traceback.format_exc(), level=logging.ERROR)
    write_file(
        outdir, 'bb_users', compress_output_files, get_users(git_conn, config.git_include_projects),
    )

    write_file(
        outdir,
        'bb_projects',
        compress_output_files,
        get_projects(git_conn, config.git_include_projects, config.git_redact_names_and_urls),
    )

    api_repos = None

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def get_and_write_repos():
        nonlocal api_repos
        # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
        api_repos, repos = zip(
            *get_repos(
                git_conn,
                config.git_include_projects,
                config.git_include_repos,
                config.git_exclude_repos,
                config.git_redact_names_and_urls,
            )
        )
        write_file(outdir, 'bb_repos', compress_output_files, repos)
        return len(api_repos)

    get_and_write_repos()

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def download_and_write_commits():
        return download_and_write_streaming(
            outdir,
            'bb_commits',
            compress_output_files,
            generator_func=get_commits_for_included_branches,
            generator_func_args=(
                git_conn,
                api_repos,
                config.git_include_branches,
                config.git_strip_text_content,
                endpoint_git_instance_info,
                config.git_redact_names_and_urls,
            ),
            item_id_dict_key='hash',
        )

    download_and_write_commits()

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
    def download_and_write_prs():
        return download_and_write_streaming(
            outdir,
            'bb_prs',
            compress_output_files,
            generator_func=get_pull_requests,
            generator_func_args=(
                git_conn,
                api_repos,
                config.git_strip_text_content,
                endpoint_git_instance_info,
                config.git_redact_names_and_urls,
            ),
            item_id_dict_key='id',
        )

    download_and_write_prs()


def _standardize_user(user):
    if not user:
        return None

    # raw user, just have email (e.g. from a commit)
    if 'id' not in user:
        return StandardizedUser(
            id=user['email'], login=user['email'], name=user['name'], email=user['email']
        )

    # API user, where github matched to a known account
    return StandardizedUser(
        id=user['id'], login=user['login'], name=user['name'], email=user['email']
    )


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def get_users(client: GithubClient, include_orgs):
    logger.info('downloading github users... [!n]')
    users = [_standardize_user(user) for org in include_orgs for user in client.get_all_users(org)]
    logger.info('✓')

    return users


def _standardize_project(api_org, redact_names_and_urls):
    return StandardizedProject(
        id=api_org['id'],
        login=api_org['login'],
        name=(
            api_org.get('name')
            if not redact_names_and_urls
            else _project_redactor.redact_name(api_org.get('name'))
        ),
        url=api_org.get('html_url') if not redact_names_and_urls else None,
    )


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def get_projects(client: GithubClient, include_orgs, redact_names_and_urls):
    logger.info('downloading github projects... [!n]')
    projects = [
        _standardize_project(client.get_organization_by_name(org), redact_names_and_urls)
        for org in include_orgs
    ]
    logger.info('✓')

    if not projects:
        raise ValueError(
            'No projects found.  Make sure your token has appropriate access to GitHub.'
        )
    return projects


def _standardize_repo(client: GithubClient, org_name, repo, redact_names_and_urls):
    return StandardizedRepository(
        id=repo['id'],
        name=(
            repo['name']
            if not redact_names_and_urls
            else _project_redactor.redact_name(repo['name'])
        ),
        full_name=(
            repo['full_name']
            if not redact_names_and_urls
            else _project_redactor.redact_name(repo['full_name'])
        ),
        url=repo['html_url'] if not redact_names_and_urls else None,
        is_fork=repo['fork'],
        default_branch_name=repo['default_branch'],
        project=_standardize_project(
            client.get_json(repo['organization']['url']), redact_names_and_urls
        ),
        branches=[
            StandardizedBranch(
                name=(
                    b['name']
                    if not redact_names_and_urls
                    else _branch_redactor.redact_name(b['name'])
                ),
                sha=b['commit']['sha'],
            )
            for b in client.get_branches(repo['full_name'])
        ],
    )


@logging_helper.log_entry_exit(logger)
def get_repos(
    client: GithubClient, include_orgs, include_repos, exclude_repos, redact_names_and_urls
):
    logger.info('downloading github repos... [!n]')

    filters = []
    if include_repos:
        filters.append(lambda r: r['name'].lower() in set([r.lower() for r in include_repos]))
    if exclude_repos:
        filters.append(lambda r: r['name'].lower() not in set([r.lower() for r in exclude_repos]))

    repos = [
        (r, _standardize_repo(client, org, r, redact_names_and_urls))
        for org in include_orgs
        for r in client.get_all_repos(org)
        if all(filt(r) for filt in filters)
    ]
    logger.info('✓')
    if not repos:
        raise ValueError(
            'No repos found. Make sure your token has appropriate access to GitHub and check your configuration of repos to pull.'
        )
    return repos


def _standardize_commit(commit, repo, branch_name, strip_text_content, redact_names_and_urls):
    author = commit.get('author') or {}
    author.update(
        {'name': commit['commit']['author']['name'], 'email': commit['commit']['author']['email']}
    )

    return StandardizedCommit(
        hash=commit['sha'],
        url=commit['html_url'] if not redact_names_and_urls else None,
        message=sanitize_text(commit['commit']['message'], strip_text_content),
        commit_date=commit['commit']['committer']['date'],
        author_date=commit['commit']['author']['date'],
        author=_standardize_user(author),
        is_merge=len(commit['parents']) > 1,
        repo=_standardize_pr_repo(repo, redact_names_and_urls),
        branch_name=branch_name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(branch_name),
    )


def _standardize_pr_repo(repo, redact_names_and_urls):
    return StandardizedShortRepository(
        id=repo['id'],
        name=(
            repo['name'] if not redact_names_and_urls else _repo_redactor.redact_name(repo['name'])
        ),
        url=repo['html_url'] if not redact_names_and_urls else None,
    )


def get_commits_for_included_branches(
    client: GithubClient,
    api_repos,
    included_branches,
    strip_text_content,
    server_git_instance_info,
    redact_names_and_urls,
):
    for i, repo in enumerate(api_repos, start=1):
        with logging_helper.log_loop_iters('repo for branch commits', i, 1):
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['organization']['login'], repo['id'], 'commits'
            )

            # Determine branches to pull commits from for this repo. If no branches are explicitly
            # provided in a config, only pull from the repo's default branch.
            # We are working with the github api object rather than a StandardizedRepository here,
            # so we can not use get_branches_for_standardized_repo as we do in bitbucket_cloud_adapter and gitlab_adapter.
            branches_to_process = [repo['default_branch']]
            additional_branch_patterns = included_branches.get(repo['name'])

            if additional_branch_patterns:
                repo_branches = [b['name'] for b in client.get_branches(repo['full_name'])]
                branches_to_process.extend(
                    get_matching_branches(additional_branch_patterns, repo_branches)
                )

            for branch in branches_to_process:
                try:
                    for j, commit in enumerate(
                        tqdm(
                            client.get_commits(
                                repo['full_name'], branch, since=pull_since, until=None
                            ),
                            desc=f'downloading commits on branch {branch} for {repo["name"]}',
                            unit='commits',
                        ),
                        start=1,
                    ):
                        with logging_helper.log_loop_iters('branch commit inside repo', j, 100):
                            yield _standardize_commit(
                                commit, repo, branch, strip_text_content, redact_names_and_urls
                            )

                except Exception as e:
                    logger.warning(f':WARN: Got exception for branch {branch}: {e}. Skipping...')


def _get_merge_commit(client: GithubClient, pr, strip_text_content, redact_names_and_urls):
    if pr['merged'] and pr['merge_commit_sha']:
        api_merge_commit = client.get_commit_by_ref(
            pr['base']['repo']['full_name'], pr['merge_commit_sha']
        )
        if api_merge_commit:
            return _standardize_commit(
                api_merge_commit,
                pr['base']['repo'],
                pr['base']['ref'],
                strip_text_content,
                redact_names_and_urls,
            )
        else:
            return None
    else:
        return None


def _standardize_pr(client: GithubClient, pr, strip_text_content, redact_names_and_urls):
    return StandardizedPullRequest(
        id=pr['number'],
        additions=pr['additions'],
        deletions=pr['deletions'],
        changed_files=pr['changed_files'],
        is_closed=pr['state'] == 'closed',
        is_merged=pr['merged'],
        created_at=pr['created_at'],
        updated_at=pr['updated_at'],
        merge_date=pr['merged_at'] if pr['merged_at'] else None,
        closed_date=pr['closed_at'] if pr['closed_at'] else None,
        title=sanitize_text(pr['title'], strip_text_content),
        body=sanitize_text(pr['body'], strip_text_content),
        url=(pr['html_url'] if not redact_names_and_urls else None),
        base_branch=(
            pr['base']['ref']
            if not redact_names_and_urls
            else _branch_redactor.redact_name(pr['base']['ref'])
        ),
        head_branch=(
            pr['head']['ref']
            if not redact_names_and_urls
            else _branch_redactor.redact_name(pr['head']['ref'])
        ),
        author=_standardize_user(client.get_json(pr['user']['url'])),
        merged_by=(
            _standardize_user(client.get_json(pr['merged_by']['url'])) if pr['merged'] else None
        ),
        commits=[
            _standardize_commit(
                c, pr['base']['repo'], pr['base']['ref'], strip_text_content, redact_names_and_urls
            )
            for c in tqdm(
                client.get_pr_commits(pr['base']['repo']['full_name'], pr['number']),
                f'downloading commits for PR {pr["number"]}',
                leave=False,
                unit='commits',
            )
        ],
        merge_commit=_get_merge_commit(client, pr, strip_text_content, redact_names_and_urls),
        comments=[
            StandardizedPullRequestComment(
                user=_standardize_user(client.get_json(c['user']['url'])),
                body=sanitize_text(c['body'], strip_text_content),
                created_at=c['created_at'],
            )
            for c in client.get_pr_comments(pr['base']['repo']['full_name'], pr['number'])
        ],
        approvals=[
            StandardizedPullRequestReview(
                user=_standardize_user(client.get_json(r['user']['url'])),
                foreign_id=r['id'],
                review_state=r['state'],
            )
            for r in client.get_pr_reviews(pr['base']['repo']['full_name'], pr['number'])
        ],
        base_repo=_standardize_pr_repo(pr['base']['repo'], redact_names_and_urls),
        head_repo=_standardize_pr_repo(pr['head']['repo'], redact_names_and_urls),
    )


def get_pull_requests(
    client: GithubClient,
    api_repos,
    strip_text_content,
    server_git_instance_info,
    redact_names_and_urls,
):
    for i, repo in enumerate(api_repos, start=1):
        with logging_helper.log_loop_iters('repo for pull requests', i, 1):
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['organization']['login'], repo['id'], 'prs'
            )
            try:
                for j, pr in enumerate(
                    tqdm(
                        client.get_pullrequests(repo['full_name']),
                        desc=f'downloading PRs for {repo["name"]}',
                        unit='prs',
                    ),
                    start=1,
                ):
                    with logging_helper.log_loop_iters('pr inside repo', j, 10):
                        updated_at = parser.parse(pr['updated_at'])

                        # PRs are ordered newest to oldest
                        # if this is too old, we're done with this repo
                        if pull_since and updated_at < pull_since:
                            break

                        yield _standardize_pr(client, pr, strip_text_content, redact_names_and_urls)

            except Exception as e:
                logger.warning(
                    f':WARN: Exception getting PRs for repo {repo["name"]}: {e}. Skipping...'
                )
