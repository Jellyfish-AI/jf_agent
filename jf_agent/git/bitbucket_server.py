from datetime import datetime
import logging
import stashy
import pytz
from tqdm import tqdm
from requests.exceptions import RetryError, ChunkedEncodingError
from urllib3.exceptions import MaxRetryError
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
    bb_conn,
):
    write_file(outdir, 'bb_users', compress_output_files, get_users(bb_conn))

    bitbucket_projects = get_projects(
        bb_conn,
        config.git_include_projects,
        config.git_exclude_projects,
        config.git_redact_names_and_urls,
    )
    if not bitbucket_projects:
        logger.warn(" No projects and repositories available to agent: Please Check Configuration")
        return
    # turn a generator that produces (api_object, dict) pairs into separate lists of API objects and dicts
    api_projects, projects = zip(*bitbucket_projects)
    write_file(outdir, 'bb_projects', compress_output_files, projects)

    api_repos = None

    @diagnostics.capture_timing()
    @logging_helper.log_entry_exit(logger)
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
    @logging_helper.log_entry_exit(logger)
    def download_and_write_commits():
        return download_and_write_streaming(
            outdir,
            'bb_commits',
            compress_output_files,
            generator_func=get_commits_for_included_branches,
            generator_func_args=(
                bb_conn,
                api_repos,
                config.git_include_branches,
                config.git_strip_text_content,
                endpoint_git_instance_info,
                config.git_redact_names_and_urls,
                config.git_verbose,
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
                bb_conn,
                api_repos,
                config.git_strip_text_content,
                endpoint_git_instance_info,
                config.git_redact_names_and_urls,
                config.git_verbose,
            ),
            item_id_dict_key='id',
        )

    download_and_write_prs()


def datetime_from_bitbucket_server_timestamp(bb_server_timestamp_str):
    return datetime.fromtimestamp(float(bb_server_timestamp_str) / 1000).replace(tzinfo=pytz.utc)


def _standardize_user(user):
    if not user:
        return None

    return {
        'id': user.get('id', ''),
        'login': user.get('name', ''),
        'name': user.get('displayName', ''),
        'email': user.get('emailAddress', ''),
    }


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def get_users(client):
    logger.info(f'downloading bitbucket users... [!n]')
    users = [_standardize_user(user) for user in client.admin.users]
    logger.info('✓')
    return users


def _standardize_project(api_project, redact_names_and_urls):
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
@logging_helper.log_entry_exit(logger)
def get_projects(client, include_projects, exclude_projects, redact_names_and_urls):
    logger.info(f'downloading bitbucket projects... [!n]')

    filters = []
    if include_projects:
        filters.append(lambda p: p['key'] in include_projects)
    if exclude_projects:
        filters.append(lambda p: p['key'] not in exclude_projects)

    projects = [
        (p, _standardize_project(p, redact_names_and_urls))
        for p in client.projects.list()
        if all(filt(p) for filt in filters)
    ]
    logger.info('✓')
    return projects


def _standardize_repo(api_project, api_repo, redact_names_and_urls):
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
        'project': _standardize_project(api_project, redact_names_and_urls),
    }


@logging_helper.log_entry_exit(logger)
def get_repos(client, api_projects, include_repos, exclude_repos, redact_names_and_urls):
    logger.info(f'downloading bitbucket repositories... [!n]')

    filters = []
    if include_repos:
        filters.append(lambda r: r['name'].lower() in set([r.lower() for r in include_repos]))
    if exclude_repos:
        filters.append(lambda r: r['name'].lower() not in set([r.lower() for r in exclude_repos]))

    for api_project in api_projects:
        project = client.projects[api_project['key']]
        for repo in project.repos.list():
            if all(filt(repo) for filt in filters):
                api_repo = project.repos[repo['name']]
                yield api_repo, _standardize_repo(api_project, api_repo, redact_names_and_urls)

    logger.info('✓')


def _standardize_commit(commit, repo, branch_name, strip_text_content, redact_names_and_urls):
    return {
        'hash': commit['id'],
        'commit_date': datetime_from_bitbucket_server_timestamp(commit['committerTimestamp']),
        'author': _standardize_user(commit['author']),
        'author_date': datetime_from_bitbucket_server_timestamp(commit['authorTimestamp']),
        'url': (
            repo['links']['self'][0]['href'].replace('browse', f'commits/{commit["id"]}')
            if not redact_names_and_urls
            else None
        ),
        'message': sanitize_text(commit.get('message'), strip_text_content),
        'is_merge': len(commit['parents']) > 1,
        'repo': _standardize_pr_repo(repo, redact_names_and_urls),
        'branch_name': branch_name
        if not redact_names_and_urls
        else _branch_redactor.redact_name(branch_name),
    }


def get_commits_for_included_branches(
    client,
    api_repos,
    included_branches,
    strip_text_content,
    server_git_instance_info,
    redact_names_and_urls,
    verbose,
):
    for i, api_repo in enumerate(api_repos, start=1):
        with logging_helper.log_loop_iters('repo for branch commits', i, 1):
            repo = api_repo.get()
            if verbose:
                logger.info(f"Beginning download of commits for repo {repo}")
            api_project = client.projects[repo['project']['key']]
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['project']['key'], repo['id'], 'commits'
            )

            # Determine branches to pull commits from for this repo. If no branches are explicitly
            # provided in a config, only pull from the repo's default branch.
            # We are working with the BBS api object rather than a StandardizedRepository here,
            # so we can not use get_branches_for_standardized_repo  as we do in bitbucket_cloud_adapter and gitlab_adapter.
            branches_to_process = [_get_default_branch_name(api_repo)]
            additional_branch_patterns = included_branches.get(api_repo.get()['name'])

            if additional_branch_patterns:
                repo_branches = [b['displayId'] for b in api_repo.branches()]
                branches_to_process.extend(
                    get_matching_branches(additional_branch_patterns, repo_branches)
                )

            for branch in branches_to_process:
                try:
                    if verbose:
                        logger.info(f"Beginning download of commits for repo {repo['name']}.")
                    commits = api_project.repos[repo['name']].commits(until=branch)

                    for j, commit in enumerate(
                        tqdm(
                            commits, desc=f'downloading commits for {repo["name"]}', unit='commits'
                        ),
                        start=1,
                    ):
                        with logging_helper.log_loop_iters('branch commit inside repo', j, 100):
                            if verbose:
                                tqdm.write(
                                    f"[{datetime.now().isoformat()}] Getting {commit['id']} ({repo['name']})"
                                )
                            standardized_commit = _standardize_commit(
                                commit, repo, branch, strip_text_content, redact_names_and_urls
                            )
                            # commits are ordered newest to oldest
                            # if this is too old, we're done with this repo
                            if pull_since and standardized_commit['commit_date'] < pull_since:
                                break

                            yield standardized_commit

                except stashy.errors.NotFoundException as e:
                    logger.warning(
                        f'WARN: Got NotFoundException for branch \"{branch}\": {e}. Skipping...'
                    )


def _standardize_pr_repo(repo, redact_names_and_urls):
    normal_repo = {
        'id': repo['id'],
        'name': (
            repo['name'] if not redact_names_and_urls else _repo_redactor.redact_name(repo['name'])
        ),
    }

    if not redact_names_and_urls:
        if 'links' in repo:
            normal_repo['url'] = repo['links']['self'][0]['href']
        elif 'url' in repo:
            normal_repo['url'] = repo['url']

    return normal_repo


def get_pull_requests(
    client, api_repos, strip_text_content, server_git_instance_info, redact_names_and_urls, verbose
):
    for i, api_repo in enumerate(api_repos, start=1):
        with logging_helper.log_loop_iters('repo for pull requests', i, 1):
            repo = api_repo.get()
            if verbose:
                logger.info(f"Beginning download of PRs for repo {repo}")
            api_project = client.projects[repo['project']['key']]
            api_repo = api_project.repos[repo['name']]
            pull_since = pull_since_date_for_repo(
                server_git_instance_info, repo['project']['key'], repo['id'], 'prs'
            )
            if verbose:
                logger.info(f"Pulling pull requests starting at {pull_since} for repo {repo}")

            skipped_prs = 0

            for pr in tqdm(
                api_repo.pull_requests.all(state='ALL', order='NEWEST'),
                desc=f'downloading PRs for {repo["name"]}',
                unit='prs',
            ):
                if verbose:
                    tqdm.write(f"[{datetime.now().isoformat()}] Processing PR {pr['id']}")
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
                except RetryError:
                    logger.warning(
                        f"Could not retrieve diff data for PR {pr['id']} in repo {api_repo.get()['name']}"
                    )
                    additions, deletions, changed_files = None, None, None
                except ChunkedEncodingError as e:
                    logger.warning(
                        f'Got ChunkedEncodingError trying to retrieve diff data for PR {pr["id"]} in repo {api_repo.get()["name"]}, error: {e}. Skipping.'
                    )
                    skipped_prs += 1
                    continue
                except stashy.errors.GenericException:
                    logger.info(
                        f'Error retrieving diff data for PR {pr["id"]} in repo {api_repo.get()["name"]}.  Skipping that PR...',
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

                activites = []
                try:
                    activites = sorted(
                        [a for a in api_pr.activities()], key=lambda x: x['createdDate']
                    )
                except (stashy.errors.GenericException, RetryError, MaxRetryError) as e:
                    logger.info(
                        f'Error retrieving activity data for PR {pr["id"]} in repo {api_repo.get()["name"]}.  Assuming no comments, approvals, etc, and continuing...\n{e}',
                    )

                for activity in activites:
                    if activity['action'] == 'COMMENTED':
                        comments.append(
                            {
                                'user': _standardize_user(activity['comment']['author']),
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
                                'user': _standardize_user(activity['user']),
                                'review_state': activity['action'],
                            }
                        )
                    elif activity['action'] == 'MERGED':
                        merge_date = datetime_from_bitbucket_server_timestamp(
                            activity['createdDate']
                        )
                        merged_by = _standardize_user(activity['user'])

                closed_date = (
                    datetime_from_bitbucket_server_timestamp(pr['closedDate'])
                    if pr.get('closedDate')
                    else None
                )

                try:
                    commits = [
                        _standardize_commit(
                            c,
                            repo,
                            pr['toRef']['displayId'],
                            strip_text_content,
                            redact_names_and_urls,
                        )
                        for c in tqdm(
                            api_pr.commits(),
                            f'downloading commits for PR {pr["id"]}',
                            leave=False,
                            unit='commits',
                        )
                    ]
                except stashy.errors.NotFoundException:
                    logger.warning(
                        f'WARN: For PR {pr["id"]}, caught stashy.errors.NotFoundException when attempting to fetch a commit'
                    )
                    commits = []

                standardized_pr = {
                    'id': pr['id'],
                    'author': _standardize_user(pr['author']['user']),
                    'title': sanitize_text(pr['title'], strip_text_content),
                    'body': sanitize_text(pr.get('description'), strip_text_content),
                    'is_closed': pr['state'] != 'OPEN',
                    'is_merged': pr['state'] == 'MERGED',
                    'created_at': datetime_from_bitbucket_server_timestamp(pr['createdDate']),
                    'updated_at': updated_at,
                    'closed_date': closed_date,
                    'url': (pr['links']['self'][0]['href'] if not redact_names_and_urls else None),
                    'base_repo': _standardize_pr_repo(
                        pr['toRef']['repository'], redact_names_and_urls
                    ),
                    'base_branch': (
                        pr['toRef']['displayId']
                        if not redact_names_and_urls
                        else _branch_redactor.redact_name(pr['toRef']['displayId'])
                    ),
                    'head_repo': _standardize_pr_repo(
                        pr['fromRef']['repository'], redact_names_and_urls
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
                    'merge_commit': None,
                }

                yield standardized_pr

            if skipped_prs > 5:
                logger.warning(
                    f'Skipped {skipped_prs} PRs in {repo["name"]}, there may be something bogus happening.',
                )


def _get_default_branch_name(api_repo):
    try:
        return api_repo.default_branch['displayId'] if api_repo.default_branch else ''
    except stashy.errors.NotFoundException:
        return ''
