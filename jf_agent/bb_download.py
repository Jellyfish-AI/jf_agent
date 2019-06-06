from datetime import datetime
import stashy
import re
import pytz
import requests
from tqdm import tqdm


class StashySession(requests.Session):
    """
    Session wrapper class, intended to intercept requests made from the stashy module.
    """

    max_retries = 3

    retry_exceptions = (
        requests.exceptions.Timeout,
        requests.exceptions.ProxyError,
        requests.exceptions.ConnectionError,
    )

    def request(self, method, url, **kwargs):

        max_retries = StashySession.max_retries

        for retries in range(1, max_retries):

            try:
                response = super().request(method, url, **kwargs)

                if response.status_code == 401:
                    print(
                        f'WARN: received 401 for the request [{method}] {url} - '
                        f'attempting to retry ({retries}/{max_retries})'
                    )
                else:
                    return response

            except StashySession.retry_exceptions as e:
                print(
                    f'WARN: received {e.__class__.__module__}.{e.__class__.__name__} '
                    f'for the request [{method}] {url} - attempting to retry ({retries}/{max_retries})'
                )

        raise requests.exceptions.RetryError(
            f'Reached the maximum number of retries for [{method}] {url}'
        )


class NameRedactor:
    def __init__(self, preserve_names=None):
        self.redacted_names = {}
        self.seq = 0
        self.preserve_names = preserve_names or []

    def redact_name(self, name):
        if name in self.preserve_names:
            return name

        redacted_name = self.redacted_names.get(name)
        if not redacted_name:
            redacted_name = f'redacted-{self.seq:04}'
            self.seq += 1
            self.redacted_names[name] = redacted_name
        return redacted_name


_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()


def datetime_from_bitbucket_server_timestamp(bb_server_timestamp_str):
    return datetime.fromtimestamp(float(bb_server_timestamp_str) / 1000).replace(tzinfo=pytz.utc)


def _normalize_user(user):
    return {
        'id': user['id'],
        'login': user['name'],
        'name': user['displayName'],
        'email': user.get('emailAddress', ''),
    }


def get_all_users(client):
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


def get_all_projects(client, include_projects, exclude_projects, redact_names_and_urls):
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


def get_all_repos(client, api_projects, include_repos, exclude_repos, redact_names_and_urls):
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


JIRA_KEY_REGEX = re.compile(r'([a-z0-9]+)[-|_|/| ]?(\d+)', re.IGNORECASE)


def _sanitize_text(text, strip_text_content):
    if not text or not strip_text_content:
        return text

    return (' ').join(
        {f'{m[0].upper().strip()}-{m[1].upper().strip()}' for m in JIRA_KEY_REGEX.findall(text)}
    )


def _normalize_commit(commit, repo, strip_text_content, redact_names_and_urls):
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
        'message': _sanitize_text(commit.get('message'), strip_text_content),
        'is_merge': len(commit['parents']) > 1,
        'repo': _normalize_pr_repo(repo, redact_names_and_urls),
    }


def get_default_branch_commits(
    client, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
):
    for api_repo in tqdm(api_repos, desc='downloading commits from all repos'):
        repo = api_repo.get()
        api_project = client.projects[repo['project']['key']]

        try:
            default_branch = api_repo.default_branch['displayId'] if api_repo.default_branch else ''
            commits = api_project.repos[repo['name']].commits(until=default_branch)

            for commit in tqdm(
                commits, desc=f'downloading commits for {repo["name"]}', leave=False
            ):
                normalized_commit = _normalize_commit(
                    commit, repo, strip_text_content, redact_names_and_urls
                )
                # commits are ordered newest to oldest; if this isn't
                # old enough, skip it and keep going
                if normalized_commit['commit_date'] >= pull_until:
                    continue

                # if this is too old, we're done with this repo
                if pull_since and normalized_commit['commit_date'] < pull_since:
                    break

                yield normalized_commit

        except stashy.errors.NotFoundException as e:
            print(
                f'WARN: Got NotFoundException for branch {repo["default_branch_name"]}: {e}. Skipping...'
            )


def _normalize_pr_repo(repo, redact_names_and_urls):
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
    client, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
):
    for api_repo in tqdm(api_repos, desc='downloading pull requests from all repos'):
        repo = api_repo.get()
        api_project = client.projects[repo['project']['key']]
        api_repo = api_project.repos[repo['name']]

        for pr in api_repo.pull_requests.all(state='ALL', order='NEWEST'):
            updated_at = datetime_from_bitbucket_server_timestamp(pr['updatedDate'])
            # PRs are ordered newest to oldest; if this isn't old enough, skip it and keep going
            if updated_at >= pull_until:
                continue

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
                            'body': _sanitize_text(activity['comment']['text'], strip_text_content),
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
                    merge_date = datetime_from_bitbucket_server_timestamp(activity['createdDate'])
                    merged_by = (_normalize_user(activity['user']),)

            closed_date = (
                datetime_from_bitbucket_server_timestamp(pr['closedDate'])
                if pr.get('closedDate')
                else None
            )

            try:
                commits = [
                    _normalize_commit(c, repo, strip_text_content, redact_names_and_urls)
                    for c in tqdm(
                        api_pr.commits(), f'downloading commits for PR {pr["id"]}', leave=False
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
                'title': _sanitize_text(pr['title'], strip_text_content),
                'is_closed': pr['state'] != 'OPEN',
                'is_merged': pr['state'] == 'MERGED',
                'created_at': datetime_from_bitbucket_server_timestamp(pr['createdDate']),
                'updated_at': updated_at,
                'closed_date': closed_date,
                'url': (pr['links']['self'][0]['href'] if not redact_names_and_urls else None),
                'base_repo': _normalize_pr_repo(pr['toRef']['repository'], redact_names_and_urls),
                'base_branch': (
                    pr['toRef']['displayId']
                    if not redact_names_and_urls
                    else _branch_redactor.redact_name(pr['toRef']['displayId'])
                ),
                'head_repo': _normalize_pr_repo(pr['fromRef']['repository'], redact_names_and_urls),
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
