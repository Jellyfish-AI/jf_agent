import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Iterable, List, Optional
from urllib.parse import quote

import requests
from jf_ingest import logging_helper
from requests.utils import default_user_agent

from jf_agent.ratelimit import RateLimiter, RateLimitRealmConfig

logger = logging.getLogger(__name__)

# One BBQL query carries at most this many allowlist entries. Each entry becomes
# an OR clause, so a bigger batch means fewer listing requests against the
# bbcloud_repos rate limit.
REPO_QUERY_BATCH_SIZE = 100

# Bitbucket does not publish a URL limit, so stay well under the ~8k that
# proxies and web servers commonly enforce. Long repo names can exhaust this
# before the batch size does, and the stricter of the two wins.
MAX_REPO_QUERY_URL_LENGTH = 6000

# A Bitbucket Cloud repo UUID, with or without the braces the API returns.
_UUID_PATTERN = re.compile(
    r'^\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?$'
)


def _escape_bbql_string(value: str) -> str:
    """Escape a value for use inside a double-quoted BBQL string literal."""
    return value.replace('\\', '\\\\').replace('"', '\\"')


def _repo_query_url(base_url: str, clauses: List[str]) -> str:
    query = '(' + ' OR '.join(clauses) + ')'
    return f'{base_url}&q=' + quote(query, safe='')


def _build_repo_query_urls(base_url: str, include_repos: Iterable[str]) -> List[str]:
    """Return the listing URLs that cover an allowlist, or [] to list unfiltered.

    Each name becomes a `~` (contains) clause rather than `=`, which is exact
    and case sensitive: the local filter matches case insensitively, so an
    exact server-side match would drop repos whose casing differs from the
    config, with no error. A contains match returns a superset instead, and the
    local filter trims it.

    A batch closes when it reaches REPO_QUERY_BATCH_SIZE entries or when one
    more clause would push the URL past MAX_REPO_QUERY_URL_LENGTH.

    The result is empty when no query can match the allowlist as safely as the
    local filter does. The caller then lists unfiltered, which is slow but
    returns every repo. Over-matching costs requests; under-matching loses
    customer data.
    """
    include_repos = list(include_repos)  # Read twice below, so never a generator.
    if any(_UUID_PATTERN.match(entry) for entry in include_repos):
        # BBQL has no case-insensitive match for a uuid, and the uuid field is
        # not reliably queryable on this endpoint.
        logger.info(
            'Listing bitbucket repos unfiltered: git_include_repos holds a repo uuid, '
            'which cannot be queried as safely as the local filter matches it'
        )
        return []

    urls = []
    batch: List[str] = []
    for entry in include_repos:
        clause = f'name~"{_escape_bbql_string(entry)}"'
        if len(_repo_query_url(base_url, [clause])) > MAX_REPO_QUERY_URL_LENGTH:
            logger.info(
                'Listing bitbucket repos unfiltered: an entry in git_include_repos is too '
                f'long to query ({len(entry)} characters)'
            )
            return []

        candidate = batch + [clause]
        if batch and (
            len(candidate) > REPO_QUERY_BATCH_SIZE
            or len(_repo_query_url(base_url, candidate)) > MAX_REPO_QUERY_URL_LENGTH
        ):
            urls.append(_repo_query_url(base_url, batch))
            batch = [clause]
        else:
            batch = candidate

    if batch:
        urls.append(_repo_query_url(base_url, batch))
    return urls


class BitbucketCloudClient:
    def __init__(self, server_base_uri, username, app_password, session):
        self.server_base_uri = server_base_uri or 'https://api.bitbucket.org'
        self.session = session
        self.session.auth = (username, app_password)
        self.rate_limiter = RateLimiter(
            {
                'bbcloud_repos': RateLimitRealmConfig(900, 60 * 60),
                'bbcloud_commits': RateLimitRealmConfig(900, 60 * 60),
            }
        )
        self.session.headers.update(
            {'Accept': 'application/json', 'User-Agent': f'jellyfish/1.0 ({default_user_agent()})'}
        )

    def get_all_repos(self, owner, include_repos: Optional[Iterable[str]] = None):
        """Yield the repos in the owner's workspace, deduplicated by uuid.

        With an allowlist, the listing runs once per batch of allowlist entries
        under a BBQL `q` filter, so the server returns only candidate repos.
        That filter matches a superset of the allowlist, and the caller's own
        filter stays the authority on what is in scope. With no allowlist, or
        when no safe query covers the allowlist, one unfiltered listing runs.
        """
        # pagelen=100 is the API max (default is 10). Listing counts against the bbcloud_repos
        # rate limit, so bigger pages matter for large workspaces. The `next` URL Bitbucket
        # returns keeps pagelen and q, so they apply to every page.
        base_url = f'{self.server_base_uri}/2.0/repositories/{owner}?role=MEMBER&pagelen=100'

        urls = _build_repo_query_urls(base_url, include_repos) if include_repos else []
        if not urls:
            yield from self.get_all_pages(base_url, rate_limit_realm='bbcloud_repos')
            return

        logger.info(f'Listing bitbucket repos for {owner} in {len(urls)} filtered request(s)')

        # A contains match can return the same repo for more than one batch.
        seen_uuids = set()
        try:
            for url in urls:
                for repo in self.get_all_pages(url, rate_limit_realm='bbcloud_repos'):
                    uuid = repo.get('uuid')
                    if uuid is not None:
                        if uuid in seen_uuids:
                            continue
                        seen_uuids.add(uuid)
                    yield repo
        except requests.exceptions.HTTPError as e:
            # A workspace that rejects the query must still ingest. A listing the
            # agent may not read at all fails again here and raises.
            logger.warning(
                f'Filtered bitbucket repo listing failed ({e}); listing {owner} unfiltered'
            )
            for repo in self.get_all_pages(base_url, rate_limit_realm='bbcloud_repos'):
                if repo.get('uuid') not in seen_uuids:
                    yield repo

    def get_forks(self, owner, repository_uuid):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/forks'
        return self.get_all_pages(url, rate_limit_realm='bbcloud_repos')

    def get_branch_by_name(self, owner, repository_uuid, branch):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/refs/branches/{branch}'
        return self.get_json(url, 'bbcloud_commits')

    def get_branches(self, owner, repository_uuid):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/refs/branches'
        return self.get_all_pages(url)  # no rate limiting

    def get_commit(self, owner, repository_uuid, sha):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/commit/{sha}'
        return self.get_json(url, 'bbcloud_commits')

    # NOTE: Not sure if these are correctly pooled under the `bbcloud_commits`
    # realm.
    def get_commit_patch(self, owner, repository_uuid, sha):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/patch/{sha}'
        return self.get_raw_text(url, 'bbcloud_commits')

    def get_commit_diff(self, owner, repository_uuid, sha):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/diff/{sha}'
        return self.get_raw_text(url, 'bbcloud_commits')

    def get_commits(self, owner, repository_uuid, branch):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/commits/{branch}'
        return self.get_all_pages(url, rate_limit_realm='bbcloud_commits', ignore404=True)

    def get_open_pullrequests(self, owner, repository_uuid):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests?state=OPEN'
        return self.get_all_pages(url, ignore404=True)  # no rate limiting

    def get_pullrequests(self, owner, repository_uuid):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests?state=OPEN&state=MERGED&state=DECLINED&state=SUPERSEDED'
        return self.get_all_pages(url, ignore404=True)  # no rate limiting

    def get_pullrequest(self, owner, repository_uuid, pull_request_id):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests/{pull_request_id}'
        return self.get_json(url)  # no rate limiting

    def pr_diff(self, owner, repository_uuid, pr_id):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests/{pr_id}/diff'
        # no rate limiting
        return self.get_raw_text(url, ignore404=True)

    def pr_comments(self, owner, repository_uuid, pr_id):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests/{pr_id}/comments'
        return self.get_all_pages(url, ignore404=True)  # no rate limiting

    def pr_activity(self, owner, repository_uuid, pr_id):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests/{pr_id}/activity'
        return self.get_all_pages(url, ignore404=True)  # no rate limiting

    def pr_commits(self, owner, repository_uuid, pr_id):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}/{repository_uuid}/pullrequests/{pr_id}/commits'
        return self.get_all_pages(url, ignore404=True)  # no rate limiting

    # Raw web service operations with optional rate limiting
    def get_json(self, url, rate_limit_realm=None):
        return self.get_raw_result(url, rate_limit_realm).json()

    def get_raw_text(self, url, rate_limit_realm=None, ignore404=False):
        try:
            return self.get_raw_result(url, rate_limit_realm).text
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404 and ignore404:
                # To stdout, not to logger; could be sensitive
                print(f'Caught a 404 for {url} - ignoring')
                return None
            raise

    def get_raw_result(self, url, rate_limit_realm=None, wait_extra=3):
        start = datetime.utcnow()
        while True:
            try:
                with self.rate_limiter.limit(rate_limit_realm):
                    result = self.session.get(url)
                    result.raise_for_status()
                    return result
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    if hasattr(e.response, 'headers') and 'Retry-After' in e.response.headers:
                        wait_time = int(e.response.headers["Retry-After"]) + wait_extra
                        logger.info(f'Retrying in {wait_time} seconds...')
                        time.sleep(wait_time)
                        continue
                    # rate-limited in spite of trying to throttle
                    # requests. No `Retry-After` returned, so
                    # We don't know how long we need to wait,
                    # so just try in 30 seconds, unless it's already
                    # been too long
                    elif (datetime.utcnow() - start) < timedelta(hours=1):
                        logger.info('Retrying in 30 seconds...')
                        time.sleep(30)
                        continue
                    else:
                        logging_helper.log_standard_error(logging.ERROR, error_code=3151)
                raise

    # Handle pagination
    def get_all_pages(self, url, rate_limit_realm=None, ignore404=False):
        current_page_values = deque()
        while True:
            if not current_page_values:
                if not url:
                    return  # exhausted the current page and there's no next page

                try:
                    page = self.get_json(url, rate_limit_realm)
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404 and ignore404:
                        # URLs are potentially sensitive data, so print instead of log!
                        print(
                            f'Caught a 404 for {url} - ignoring',
                        )
                        return
                    raise

                if 'values' in page:
                    current_page_values.extend(page['values'])
                    if not current_page_values:
                        return  # no new values returned

                url = page['next'] if 'next' in page else None

            yield current_page_values.popleft()
