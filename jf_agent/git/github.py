from dateutil import parser
import logging
from tqdm import tqdm

from jf_agent.git import (
    GithubClient,
    NormalizedCommit,
    NormalizedProject,
    NormalizedPullRequest,
    NormalizedPullRequestReview,
    NormalizedRepository,
    NormalizedShortRepository,
    NormalizedUser,
)
from jf_agent.git import pull_since_date_for_repo
from jf_agent.name_redactor import NameRedactor, sanitize_text
from jf_agent import agent_logging, diagnostics, download_and_write_streaming, write_file
from jf_agent.config_file_reader import GitConfig

logger = logging.getLogger(__name__)

_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def load_and_dump(
    config: GitConfig,
    outdir: str,
    compress_output_files: bool,
    endpoint_git_instance_info: dict,
    git_conn,
):
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
    @agent_logging.log_entry_exit(logger)
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
    @agent_logging.log_entry_exit(logger)
    def download_and_write_commits():
        return download_and_write_streaming(
            outdir,
            'bb_commits',
            compress_output_files,
            generator_func=get_default_branch_commits,
            generator_func_args=(
                git_conn,
                api_repos,
                config.git_strip_text_content,
                endpoint_git_instance_info,
                config.git_redact_names_and_urls,
            ),
            item_id_dict_key='hash',
        )

    download_and_write_commits()

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
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


def _normalize_user(user):
    if not user:
        return None

    # raw user, just have email (e.g. from a commit)
    if 'id' not in user:
        return NormalizedUser(
            id=user['email'], login=user['email'], name=user['name'], email=user['email']
        )

    # API user, where github matched to a known account
    return NormalizedUser(
        id=user['id'], login=user['login'], name=user['name'], email=user['email']
    )


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def get_users(client: GithubClient, include_orgs):
    print('downloading github users... ', end='', flush=True)
    users = [_normalize_user(user) for org in include_orgs for user in client.get_all_users(org)]
    print('✓')

    return users


def _normalize_project(api_org, redact_names_and_urls):
    return NormalizedProject(
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
@agent_logging.log_entry_exit(logger)
def get_projects(client: GithubClient, include_orgs, redact_names_and_urls):
    print('downloading github projects... ', end='', flush=True)
    projects = [
        _normalize_project(client.get_organization_by_name(org), redact_names_and_urls)
        for org in include_orgs
    ]
    print('✓')

    if not projects:
        raise ValueError(
            'No projects found.  Make sure your token has appropriate access to GitHub.'
        )
    return projects


def _normalize_repo(client: GithubClient, org_name, repo, redact_names_and_urls):
    return NormalizedRepository(
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
        is_forked=repo['fork'],
        default_branch_name=repo['default_branch'],
        project=_normalize_project(
            client.get_json(repo['organization']['url']), redact_names_and_urls
        ),
        branches=[
            {
                'name': (
                    b['name']
                    if not redact_names_and_urls
                    else _branch_redactor.redact_name(b['name'])
                ),
                'sha': b['commit']['sha'],
            }
            for b in client.get_branches(repo['full_name'])
        ],
    )


@agent_logging.log_entry_exit(logger)
def get_repos(
    client: GithubClient, include_orgs, include_repos, exclude_repos, redact_names_and_urls
):
    print('downloading github repos... ', end='', flush=True)

    filters = []
    if include_repos:
        filters.append(lambda r: r['name'] in include_repos)
    if exclude_repos:
        filters.append(lambda r: r['name'] not in exclude_repos)

    repos = [
        (r, _normalize_repo(client, org, r, redact_names_and_urls))
        for org in include_orgs
        for r in client.get_all_repos(org)
        if all(filt(r) for filt in filters)
    ]
    print('✓')
    if not repos:
        raise ValueError(
            'No repos found. Make sure your token has appropriate access to GitHub and check your configuration of repos to pull.'
        )
    return repos


def _normalize_commit(commit, repo, strip_text_content, redact_names_and_urls):
    author = commit.get('author') or {}
    author.update(
        {'name': commit['commit']['author']['name'], 'email': commit['commit']['author']['email']}
    )

    return NormalizedCommit(
        hash=commit['sha'],
        url=commit['html_url'] if not redact_names_and_urls else None,
        message=sanitize_text(commit['commit']['message'], strip_text_content),
        commit_date=commit['commit']['committer']['date'],
        author_date=commit['commit']['author']['date'],
        author=_normalize_user(author),
        is_merged=len(commit['parents']) > 1,
        repo=_normalize_pr_repo(repo, redact_names_and_urls),
    )


def _normalize_pr_repo(repo, redact_names_and_urls):
    return NormalizedShortRepository(
        id=repo['id'],
        name=(
            repo['name'] if not redact_names_and_urls else _repo_redactor.redact_name(repo['name'])
        ),
        url=repo['html_url'] if not redact_names_and_urls else None,
    )


def get_default_branch_commits(
    client: GithubClient,
    api_repos,
    strip_text_content,
    server_git_instance_info,
    redact_names_and_urls,
):
    for i, repo in enumerate(api_repos, start=1):
        with agent_logging.log_loop_iters(logger, 'repo for branch commits', i, 1):
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['organization']['login'], repo['id'], 'commits'
            )
            try:
                for j, commit in enumerate(
                    tqdm(
                        client.get_commits(
                            repo['full_name'], repo['default_branch'], since=pull_since, until=None
                        ),
                        desc=f'downloading commits for {repo["name"]}',
                        unit='commits',
                    ),
                    start=1,
                ):
                    with agent_logging.log_loop_iters(logger, 'branch commit inside repo', j, 100):
                        yield _normalize_commit(
                            commit, repo, strip_text_content, redact_names_and_urls
                        )

            except Exception as e:
                print(f':WARN: Got exception for branch {repo["default_branch"]}: {e}. Skipping...')


def _get_merge_commit(client: GithubClient, pr, strip_text_content, redact_names_and_urls):
    if pr['merged'] and pr['merge_commit_sha']:
        api_merge_commit = client.get_commit_by_ref(
            pr['base']['repo']['full_name'], pr['merge_commit_sha']
        )
        if api_merge_commit:
            return _normalize_commit(
                api_merge_commit, pr['base']['repo'], strip_text_content, redact_names_and_urls
            )
        else:
            return None
    else:
        return None


def _normalize_pr(client: GithubClient, pr, strip_text_content, redact_names_and_urls):
    return NormalizedPullRequest(
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
        author=_normalize_user(client.get_json(pr['user']['url'])),
        merged_by=(
            _normalize_user(client.get_json(pr['merged_by']['url'])) if pr['merged'] else None
        ),
        commits=[
            _normalize_commit(c, pr['base']['repo'], strip_text_content, redact_names_and_urls)
            for c in tqdm(
                client.get_pr_commits(pr['base']['repo']['full_name'], pr['number']),
                f'downloading commits for PR {pr["number"]}',
                leave=False,
                unit='commits',
            )
        ],
        merge_commit=_get_merge_commit(client, pr, strip_text_content, redact_names_and_urls),
        comments=[
            {
                'user': _normalize_user(client.get_json(c['user']['url'])),
                'body': c['body'],
                'created_at': c['created_at'],
            }
            for c in client.get_pr_comments(pr['base']['repo']['full_name'], pr['number'])
        ],
        approvals=[
            NormalizedPullRequestReview(
                user=_normalize_user(client.get_json(r['user']['url'])),
                foreign_id=r['id'],
                review_state=r['state'],
            )
            for r in client.get_pr_reviews(pr['base']['repo']['full_name'], pr['number'])
        ],
        baase_repo=_normalize_pr_repo(pr['base']['repo'], redact_names_and_urls),
        head_repo=_normalize_pr_repo(pr['head']['repo'], redact_names_and_urls),
    )


def get_pull_requests(
    client: GithubClient,
    api_repos,
    strip_text_content,
    server_git_instance_info,
    redact_names_and_urls,
):
    for i, repo in enumerate(api_repos, start=1):
        with agent_logging.log_loop_iters(logger, 'repo for pull requests', i, 1):
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
                    with agent_logging.log_loop_iters(logger, 'pr inside repo', j, 10):
                        updated_at = parser.parse(pr['updated_at'])

                        # PRs are ordered newest to oldest
                        # if this is too old, we're done with this repo
                        if pull_since and updated_at < pull_since:
                            break

                        yield _normalize_pr(client, pr, strip_text_content, redact_names_and_urls)

            except Exception as e:
                print(f':WARN: Exception getting PRs for repo {repo["name"]}: {e}. Skipping...')
    print()
