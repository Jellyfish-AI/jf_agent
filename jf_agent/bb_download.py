from datetime import datetime
from tqdm import tqdm
import stashy
import re


def datetime_from_bitbucket_server_timestamp(bb_server_timestamp_str):
    return datetime.fromtimestamp(float(bb_server_timestamp_str) / 1000)


def _normalize_user(user):
    return {
        'id': user['id'],
        'login': user['name'],
        'name': user['displayName'],
        'email': user.get('emailAddress', ''),
    }


def get_all_users(client):
    print('downloading bitbucket users... ✓')

    return [_normalize_user(user) for user in client.admin.users]


def get_all_projects(client, include_projects, exclude_projects):
    print('downloading bitbucket projects... ✓')

    filters = []
    if include_projects:
        filters.append(lambda p: p['key'] in include_projects)
    if exclude_projects:
        filters.append(lambda p: p['key'] not in exclude_projects)

    return [
        {
            'id': p['id'],
            'login': p['key'],
            'name': p.get('name'),
            'description': p.get('description'),
            'url': p['links']['self'][0]['href'],
        }
        for p in client.projects.list()
        if all(filt(p) for filt in filters)
    ]


def get_all_repos(client, projects, include_repos, exclude_repos):
    print('downloading bitbucket repositories... ', end='', flush=True)

    filters = []
    if include_repos:
        filters.append(lambda r: r['name'] in include_repos)
    if exclude_repos:
        filters.append(lambda r: r['name'] not in exclude_repos)

    api_repos = (
        api_project.repos[repo['name']]
        for project in projects
        for api_project in [client.projects[project['login']]]
        for repo in api_project.repos.list()
        if all(filt(repo) for filt in filters)
    )

    for api_repo in api_repos:
        try:
            default_branch_name = (
                api_repo.default_branch['displayId'] if api_repo.default_branch else ''
            )
        except stashy.errors.NotFoundException:
            default_branch_name = ''

        branches = list(
            {'name': b['displayId'], 'sha': b['latestCommit']} for b in api_repo.branches()
        )

        yield {
            'id': repo['id'],
            'name': repo['name'],
            'full_name': repo['name'],
            'description': repo['name'],
            'url': repo['links']['self'][0]['href'],
            'default_branch_name': default_branch_name,
            'branches': branches,
            'is_fork': 'origin' in repo,
            'project': project,
        }

    print('✓')


JIRA_KEY_REGEX = re.compile(r'([a-z0-9]+)[-|_|/| ]?(\d+)', re.IGNORECASE)


def _sanitize_text(text, strip_text_content):
    if not text or not strip_text_content:
        return text

    return (' ').join(
        {f'{m[0].upper().strip()}-{m[1].upper().strip()}' for m in JIRA_KEY_REGEX.findall(text)}
    )


def _normalize_commit(commit, repo, strip_text_content):
    return {
        'hash': commit['id'],
        'commit_date': datetime_from_bitbucket_server_timestamp(commit['committerTimestamp']),
        'author': commit['author'],
        'author_date': datetime_from_bitbucket_server_timestamp(commit['authorTimestamp']),
        'url': repo['url'].replace('browse', f'commits/{commit["id"]}'),
        'message': _sanitize_text(commit.get('message'), strip_text_content),
        'is_merge': len(commit['parents']) > 1,
        'repo': _normalize_pr_repo(repo),
    }


def get_default_branch_commits(client, repos, strip_text_content):
    for repo in repos:
        api_project = client.projects[repo['project']['login']]

        try:
            commits = api_project.repos[repo['name']].commits(until=repo['default_branch_name'])

            for commit in tqdm(
                commits,
                desc=f'downloading {repo["project"]["login"]}/{repo["name"]} commits',
                unit='commit',
            ):
                yield _normalize_commit(commit, repo, strip_text_content)

        except stashy.errors.NotFoundException as e:
            print(f'WARN: Got NotFoundException for branch {repo["default_branch_name"]}: {e}')
            return []


def _normalize_pr_repo(repo):
    normal_repo = {'id': repo['id'], 'name': repo['name']}

    if 'links' in repo:
        normal_repo['url'] = repo['links']['self'][0]['href']
    elif 'url' in repo:
        normal_repo['url'] = repo['url']

    return normal_repo


def get_pull_requests(client, repos, strip_text_content):
    print('downloading bitbucket PRs... ', end='', flush=True)

    for repo in repos:
        api_project = client.projects[repo['project']['login']]
        api_repo = api_project.repos[repo['name']]

        for pr in tqdm(
            api_repo.pull_requests.all(state='ALL', order='NEWEST'),
            desc=f'downloading PRs for {repo["project"]["login"]}/{repo["name"]}',
            unit='pr',
        ):

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
                commits = [_normalize_commit(c, repo, strip_text_content) for c in api_pr.commits()]
            except stashy.errors.NotFoundException:
                print(
                    f'WARN: For PR {pr["id"]}, caught stashy.errors.NotFoundException when attempting to fetch a commit'
                )
                commits = []

            yield {
                'id': pr['id'],
                'author': _normalize_user(pr['author']['user']),
                'title': _sanitize_text(pr['title'], strip_text_content),
                'is_closed': pr['state'] != 'OPEN',
                'is_merged': pr['state'] == 'MERGED',
                'created_at': datetime_from_bitbucket_server_timestamp(pr['createdDate']),
                'updated_at': datetime_from_bitbucket_server_timestamp(pr['updatedDate']),
                'closed_date': closed_date,
                'url': pr['links']['self'][0]['href'],
                'base_repo': _normalize_pr_repo(pr['toRef']['repository']),
                'base_branch': pr['toRef']['displayId'],
                'head_repo': _normalize_pr_repo(pr['fromRef']['repository']),
                'head_branch': pr['fromRef']['displayId'],
                'additions': additions,
                'deletions': deletions,
                'changed_files': changed_files,
                'comments': comments,
                'approvals': approvals,
                'merge_date': merge_date,
                'merged_by': merged_by,
                'commits': commits,
            }

    print('✓')
