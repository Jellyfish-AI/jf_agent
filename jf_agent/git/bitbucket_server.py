from datetime import datetime
import logging
import stashy
import pytz
from tqdm import tqdm
from requests.exceptions import RetryError
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
    bb_conn,
):
    write_file(outdir, 'bb_users', compress_output_files, get_users(bb_conn))

    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_projects, projects = zip(
        *get_projects(
            bb_conn,
            config.git_include_projects,
            config.git_exclude_projects,
            config.git_redact_names_and_urls,
        )
    )
    write_file(outdir, 'bb_projects', compress_output_files, projects)

    api_repos = None

    @diagnostics.capture_timing()
    @agent_logging.log_entry_exit(logger)
    def get_and_write_repos():
        nonlocal api_repos
        # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
        api_repos, repos = zip(
            *get_repos(
                bb_conn,
                api_projects,
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
                bb_conn,
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
                bb_conn,
                api_repos,
                config.git_strip_text_content,
                endpoint_git_instance_info,
                config.git_redact_names_and_urls,
            ),
            item_id_dict_key='id',
        )

    download_and_write_prs()


def datetime_from_bitbucket_server_timestamp(bb_server_timestamp_str):
    return datetime.fromtimestamp(float(bb_server_timestamp_str) / 1000).replace(tzinfo=pytz.utc)


def _normalize_user(user):
    if not user:
        return None

    return {
        'id': user.get('id', ''),
        'login': user.get('name', ''),
        'name': user.get('displayName', ''),
        'email': user.get('emailAddress', ''),
    }


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def get_users(client):
    print('downloading bitbucket users... ', end='', flush=True)
    users = [_normalize_user(user) for user in client.admin.users]
    print('✓')
    return users


def _normalize_project(api_project, redact_names_and_urls):
    return {
        'id': api_project['id'],
        'login': api_project['key'],
        'name': (
            api_project.get('name')
            if not redact_names_and_urls
            else _project_redactor.redact_name(api_project.get('name'))
        ),
        'url': api_project['links']['self'][0]['href'] if not redact_names_and_urls else None,
    }


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def get_projects(client, include_projects, exclude_projects, redact_names_and_urls):
    print('downloading bitbucket projects... ', end='', flush=True)

    filters = []
    if include_projects:
        filters.append(lambda p: p['key'] in include_projects)
    if exclude_projects:
        filters.append(lambda p: p['key'] not in exclude_projects)

    projects = [
        (p, _normalize_project(p, redact_names_and_urls))
        for p in client.projects.list()
        if all(filt(p) for filt in filters)
    ]
    print('✓')
    return projects


def _normalize_repo(api_project, api_repo, redact_names_and_urls):
    repo = api_repo.get()

    name = (
        repo['name'] if not redact_names_and_urls else _project_redactor.redact_name(repo['name'])
    )
    url = repo['links']['self'][0]['href'] if not redact_names_and_urls else None
    try:
        default_branch_name = (
            api_repo.default_branch['displayId'] if api_repo.default_branch else ''
        )
    except stashy.errors.NotFoundException:
        default_branch_name = ''

    branches = [
        {
            'name': (
                b['displayId']
                if not redact_names_and_urls
                else _branch_redactor.redact_name(b['displayId'])
            ),
            'sha': b['latestCommit'],
        }
        for b in api_repo.branches()
    ]

    return {
        'id': repo['id'],
        'name': name,
        'full_name': name,
        'url': url,
        'default_branch_name': default_branch_name,
        'branches': branches,
        'is_fork': 'origin' in repo,
        'project': _normalize_project(api_project, redact_names_and_urls),
    }


@agent_logging.log_entry_exit(logger)
def get_repos(client, api_projects, include_repos, exclude_repos, redact_names_and_urls):
    print('downloading bitbucket repositories... ', end='', flush=True)

    filters = []
    if include_repos:
        filters.append(lambda r: r['name'] in include_repos)
    if exclude_repos:
        filters.append(lambda r: r['name'] not in exclude_repos)

    for api_project in api_projects:
        project = client.projects[api_project['key']]
        for repo in project.repos.list():
            if all(filt(repo) for filt in filters):
                api_repo = project.repos[repo['name']]
                yield api_repo, _normalize_repo(api_project, api_repo, redact_names_and_urls)

    print('✓')


def _normalize_commit(commit, repo, api_project, strip_text_content, redact_names_and_urls):
    return {
        'hash': commit['id'],
        'commit_date': datetime_from_bitbucket_server_timestamp(commit['committerTimestamp']),
        'author': commit['author'],
        'author_date': datetime_from_bitbucket_server_timestamp(commit['authorTimestamp']),
        'url': (
            repo['links']['self'][0]['href'].replace('browse', f'commits/{commit["id"]}')
            if not redact_names_and_urls
            else None
        ),
        'message': sanitize_text(commit.get('message'), strip_text_content),
        'is_merge': len(commit['parents']) > 1,
        'repo': _normalize_pr_repo(api_project=api_project, repo=repo, redact_names_and_urls=redact_names_and_urls),
    }


def get_default_branch_commits(
    client, api_repos, strip_text_content, server_git_instance_info, redact_names_and_urls
):
    for i, api_repo in enumerate(api_repos, start=1):
        with agent_logging.log_loop_iters(logger, 'repo for branch commits', i, 1):
            repo = api_repo.get()
            api_project = client.projects[repo['project']['key']]
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['project']['key'], repo['id'], 'commits'
            )
            try:

                default_branch = (
                    api_repo.default_branch['displayId'] if api_repo.default_branch else ''
                )
                commits = api_project.repos[repo['name']].commits(until=default_branch)

                for j, commit in enumerate(
                    tqdm(commits, desc=f'downloading commits for {repo["name"]}', unit='commits'),
                    start=1,
                ):
                    with agent_logging.log_loop_iters(logger, 'branch commit inside repo', j, 100):
                        normalized_commit = _normalize_commit(
                            commit=commit,
                            repo=repo,
                            api_project=api_project,
                            strip_text_content=strip_text_content,
                            redact_names_and_urls=redact_names_and_urls,
                        )
                        # commits are ordered newest to oldest
                        # if this is too old, we're done with this repo
                        if pull_since and normalized_commit['commit_date'] < pull_since:
                            break

                        yield normalized_commit

            except stashy.errors.NotFoundException as e:
                print(
                    f'WARN: Got NotFoundException for branch \"{repo.get("default_branch_name", "")}\": {e}. Skipping...'
                )


def _normalize_pr_repo(api_project, repo, redact_names_and_urls):
    normal_repo = {
        'id': repo['id'],
        'name': (
            repo['name'] if not redact_names_and_urls else _repo_redactor.redact_name(repo['name'])
        ),
        'project': _normalize_project(api_project, redact_names_and_urls)
    }

    if not redact_names_and_urls:
        if 'links' in repo:
            normal_repo['url'] = repo['links']['self'][0]['href']
        elif 'url' in repo:
            normal_repo['url'] = repo['url']

    return normal_repo


def get_pull_requests(
    client, api_repos, strip_text_content, server_git_instance_info, redact_names_and_urls
):
    for i, api_repo in enumerate(api_repos, start=1):
        with agent_logging.log_loop_iters(logger, 'repo for pull requests', i, 1):
            repo = api_repo.get()
            api_project = client.projects[repo['project']['key']]
            api_repo = api_project.repos[repo['name']]
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['project']['key'], repo['id'], 'prs'
            )
            for pr in tqdm(
                api_repo.pull_requests.all(state='ALL', order='NEWEST'),
                desc=f'downloading PRs for {repo["name"]}',
                unit='prs',
            ):
                updated_at = datetime_from_bitbucket_server_timestamp(pr['updatedDate'])
                # PRs are ordered newest to oldest
                # if this is too old, we're done with this repo
                if pull_since and updated_at < pull_since:
                    break

                api_pr = api_repo.pull_requests[pr['id']]

                try:
                    pr_diffs = api_pr.diff().diffs
                except TypeError:
                    additions, deletions, changed_files = None, None, None
                except stashy.errors.NotFoundException:
                    additions, deletions, changed_files = None, None, None
                except RetryError as e:
                    print(f"Could not retrieve diff data for {pr['id']}")
                    additions, deletions, changed_files = None, None, None
                except stashy.errors.GenericException as e:
                    agent_logging.log_and_print(
                        logger,
                        logging.INFO,
                        f'Got error {e} on diffs for repo {pr["id"]}, skipping...',
                    )
                    additions, deletions, changed_files = None, None, None
                else:
                    additions, deletions, changed_files = 0, 0, 0

                    for pr_diff in pr_diffs:
                        changed_files += 1
                        for hunk in pr_diff.hunks:
                            for segment in hunk['segments']:
                                if segment['type'] == 'ADDED':
                                    additions += len(segment['lines'])
                                if segment['type'] == 'REMOVED':
                                    deletions += len(segment['lines'])

                comments = []
                approvals = []
                merge_date = None
                merged_by = None

                for activity in sorted(
                    [a for a in api_pr.activities()], key=lambda x: x['createdDate']
                ):
                    if activity['action'] == 'COMMENTED':
                        comments.append(
                            {
                                'user': _normalize_user(activity['comment']['author']),
                                'body': sanitize_text(
                                    activity['comment']['text'], strip_text_content
                                ),
                                'created_at': datetime_from_bitbucket_server_timestamp(
                                    activity['comment']['createdDate']
                                ),
                            }
                        )
                    elif activity['action'] in ('APPROVED', 'REVIEWED'):
                        approvals.append(
                            {
                                'foreign_id': activity['id'],
                                'user': _normalize_user(activity['user']),
                                'review_state': activity['action'],
                            }
                        )
                    elif activity['action'] == 'MERGED':
                        merge_date = datetime_from_bitbucket_server_timestamp(
                            activity['createdDate']
                        )
                        merged_by = _normalize_user(activity['user'])

                closed_date = (
                    datetime_from_bitbucket_server_timestamp(pr['closedDate'])
                    if pr.get('closedDate')
                    else None
                )

                try:
                    commits = [
                        _normalize_commit(
                            commit=c,
                            repo=repo,
                            api_project=api_project,
                            strip_text_content=strip_text_content,
                            redact_names_and_urls=redact_names_and_urls
                        )
                        for c in tqdm(
                            api_pr.commits(),
                            f'downloading commits for PR {pr["id"]}',
                            leave=False,
                            unit='commits',
                        )
                    ]
                except stashy.errors.NotFoundException:
                    print(
                        f'WARN: For PR {pr["id"]}, caught stashy.errors.NotFoundException when attempting to fetch a commit'
                    )
                    commits = []

                normalized_pr = {
                    'id': pr['id'],
                    'author': _normalize_user(pr['author']['user']),
                    'title': sanitize_text(pr['title'], strip_text_content),
                    'body': sanitize_text(pr.get('description'), strip_text_content),
                    'is_closed': pr['state'] != 'OPEN',
                    'is_merged': pr['state'] == 'MERGED',
                    'created_at': datetime_from_bitbucket_server_timestamp(pr['createdDate']),
                    'updated_at': updated_at,
                    'closed_date': closed_date,
                    'url': (pr['links']['self'][0]['href'] if not redact_names_and_urls else None),
                    'base_repo': _normalize_pr_repo(
                        api_project=api_project,
                        repo=pr['toRef']['repository'],
                        redact_names_and_urls=redact_names_and_urls
                    ),
                    'base_branch': (
                        pr['toRef']['displayId']
                        if not redact_names_and_urls
                        else _branch_redactor.redact_name(pr['toRef']['displayId'])
                    ),
                    'head_repo': _normalize_pr_repo(
                        api_project=api_project,
                        repo=pr['fromRef']['repository'],
                        redact_names_and_urls=redact_names_and_urls
                    ),
                    'head_branch': (
                        pr['fromRef']['displayId']
                        if not redact_names_and_urls
                        else _branch_redactor.redact_name(pr['fromRef']['displayId'])
                    ),
                    'additions': additions,
                    'deletions': deletions,
                    'changed_files': changed_files,
                    'comments': comments,
                    'approvals': approvals,
                    'merge_date': merge_date,
                    'merged_by': merged_by,
                    'commits': commits,
                }

                yield normalized_pr
