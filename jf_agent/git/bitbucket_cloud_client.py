from collections import deque
from datetime import datetime, timedelta
import logging
import time
from typing import Optional

import requests
from requests.utils import default_user_agent

from jf_agent.ratelimit import RateLimiter, RateLimitRealmConfig
from jf_ingest import logging_helper

logger = logging.getLogger(__name__)


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

    def get_all_repos(self, owner):
        url = f'{self.server_base_uri}/2.0/repositories/{owner}?role=MEMBER'
        return self.get_all_pages(url, rate_limit_realm='bbcloud_repos')

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
                        print(f'Caught a 404 for {url} - ignoring',)
                        return
                    raise

                if 'values' in page:
                    current_page_values.extend(page['values'])
                    if not current_page_values:
                        return  # no new values returned

                url = page['next'] if 'next' in page else None

            yield current_page_values.popleft()
