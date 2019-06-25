from tqdm import tqdm
import pytz
from github import GithubException
from dateutil import parser

from jf_agent.name_redactor import NameRedactor, sanitize_text


_branch_redactor = NameRedactor(preserve_names=['master', 'develop'])
_project_redactor = NameRedactor()
_repo_redactor = NameRedactor()


def _normalize_user(user):
    if not user:
        return None

    # raw user, just have email (e.g. from a commit)
    if type(user) == dict:
        return {
            'id': user['email'],
            'login': user['email'],
            'name': user['name'],
            'email': user['email'],
        }

    # API user, where github matched to a known account
    return {'id': user.id, 'login': user.login, 'name': user.name, 'email': user.email}


def get_all_users(client, include_orgs):
    print('downloading github users... ', end='', flush=True)
    users = [
        _normalize_user(user)
        for org in include_orgs
        for user in client.get_organization(org).get_members()
    ]
    print('✓')
    return users


def _normalize_project(api_org, redact_names_and_urls):
    return {
        'id': api_org.id,
        'login': api_org.login,
        'name': (
            api_org.name
            if not redact_names_and_urls
            else _project_redactor.redact_name(api_org.name)
        ),
        'url': api_org.html_url if not redact_names_and_urls else None,
    }


def get_all_projects(client, include_orgs, redact_names_and_urls):
    print('downloading github projects... ', end='', flush=True)
    projects = [
        _normalize_project(client.get_organization(org), redact_names_and_urls)
        for org in include_orgs
    ]
    print('✓')
    return projects


def _normalize_repo(repo, redact_names_and_urls):
    return {
        'id': repo.id,
        'name': (
            repo.name if not redact_names_and_urls else _project_redactor.redact_name(repo.name)
        ),
        'full_name': (
            repo.full_name
            if not redact_names_and_urls
            else _project_redactor.redact_name(repo.full_name)
        ),
        'url': repo.html_url if not redact_names_and_urls else None,
        'default_branch_name': repo.default_branch,
        'branches': [
            {
                'name': (
                    b.name if not redact_names_and_urls else _branch_redactor.redact_name(b.name)
                ),
                'sha': b.commit.sha,
            }
            for b in repo.get_branches()
        ],
        'is_fork': repo.fork,
        'project': _normalize_project(repo.organization, redact_names_and_urls),
    }


def get_all_repos(client, include_orgs, include_repos, exclude_repos, redact_names_and_urls):
    print('downloading github repos... ', end='', flush=True)

    filters = []
    if include_repos:
        filters.append(lambda r: r.name in include_repos)
    if exclude_repos:
        filters.append(lambda r: r.name not in exclude_repos)

    repos = [
        (r, _normalize_repo(r, redact_names_and_urls))
        for org in include_orgs
        for r in client.get_organization(org).get_repos()
        if all(filt(r) for filt in filters)
    ]
    print('✓')
    return repos


def _normalize_commit(commit, repo, strip_text_content, redact_names_and_urls):
    author = commit.author or {
        'name': commit._rawData['commit']['author']['name'],
        'email': commit._rawData['commit']['author']['email'],
    }

    return {
        'hash': commit.sha,
        'commit_date': parser.parse(commit._rawData['commit']['committer']['date']),
        'author': _normalize_user(author),
        'author_date': parser.parse(commit._rawData['commit']['author']['date']),
        'url': commit.html_url if not redact_names_and_urls else None,
        'message': sanitize_text(commit.commit.message, strip_text_content),
        'is_merge': len(commit.parents) > 1,
        'repo': _normalize_pr_repo(repo, redact_names_and_urls),
    }


def _normalize_pr_repo(repo, redact_names_and_urls):
    return {
        'id': repo.id,
        'name': (repo.name if not redact_names_and_urls else _repo_redactor.redact_name(repo.name)),
        'url': (repo.html_url if not redact_names_and_urls else None,),
    }


def get_default_branch_commits(
    client, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
):
    for repo in api_repos:
        try:
            for commit in tqdm(
                repo.get_commits(
                    sha=repo.get_branch(repo.default_branch).commit.sha,
                    since=pull_since,
                    until=pull_until,
                ),
                desc=f'downloading commits for {repo.name}',
                unit='commit',
            ):
                yield _normalize_commit(commit, repo, strip_text_content, redact_names_and_urls)

        except GithubException as e:
            print(f':WARN: Got exception for branch {repo.default_branch}: {e}. Skipping...')


def _normalize_pr(pr, repo, strip_text_content, redact_names_and_urls):
    return {
        'id': pr.number,
        'author': _normalize_user(pr.user),
        'title': sanitize_text(pr.title, strip_text_content),
        'body': sanitize_text(pr.body, strip_text_content),
        'is_closed': pr.state == 'closed',
        'is_merged': pr.merged,
        'created_at': pr.created_at.replace(tzinfo=pytz.utc),
        'updated_at': pr.updated_at.replace(tzinfo=pytz.utc),
        'closed_date': pr.closed_at.replace(tzinfo=pytz.utc) if pr.closed_at else None,
        'url': (pr.html_url if not redact_names_and_urls else None),
        'base_repo': _normalize_pr_repo(pr.base.repo, redact_names_and_urls),
        'base_branch': (
            pr.base.ref if not redact_names_and_urls else _branch_redactor.redact_name(pr.base.ref)
        ),
        'head_repo': _normalize_pr_repo(pr.head.repo, redact_names_and_urls),
        'head_branch': (
            pr.head.ref if not redact_names_and_urls else _branch_redactor.redact_name(pr.head.ref)
        ),
        'additions': pr.additions,
        'deletions': pr.deletions,
        'changed_files': pr.changed_files,
        'comments': [
            {
                'user': _normalize_user(c.user),
                'body': c.body,
                'created_at': c.created_at.replace(tzinfo=pytz.utc),
            }
            for c in pr.get_comments()
        ],
        'approvals': [
            {'foreign_id': r.id, 'user': _normalize_user(r.user), 'review_state': r.state}
            for r in pr.get_reviews()
        ],
        'merge_date': pr.merged_at.replace(tzinfo=pytz.utc) if pr.merged_at else None,
        'merged_by': _normalize_user(pr.merged_by),
        'commits': [
            _normalize_commit(c, repo, strip_text_content, redact_names_and_urls)
            for c in tqdm(
                pr.get_commits(),
                f'downloading commits for PR {pr.number}',
                leave=False,
                unit='commit',
            )
        ],
    }


def get_pull_requests(
    client, api_repos, strip_text_content, pull_since, pull_until, redact_names_and_urls
):

    for repo in api_repos:
        try:
            for pr in tqdm(
                repo.get_pulls(state='all', sort='updated', direction='desc'),
                desc=f'downloading PRs for {repo.name}',
                unit='pr',
            ):
                normalized_pr = _normalize_pr(pr, repo, strip_text_content, redact_names_and_urls)

                # PRs are ordered newest to oldest; if this isn't old enough, skip it and keep going
                if normalized_pr['updated_at'] >= pull_until:
                    continue

                # if this is too old, we're done with this repo
                if pull_since and normalized_pr['updated_at'] < pull_since:
                    break

                yield normalized_pr

        except GithubException as e:
            print(f':WARN: Exception getting PRs for repo {repo.name}: {e}. Skipping...')
    print()
