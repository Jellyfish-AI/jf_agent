from jf_agent.session import retry_session
from requests.utils import default_user_agent
import requests

from collections import deque
from datetime import datetime
import time
import pytz
import logging

logger = logging.getLogger(__name__)


class GithubClient:
    def __init__(self, token, base_url=None, verify=True, session=None, **kwargs):
        self.token = token
        self.base_url = base_url or 'https://api.github.com'

        self.session = session or retry_session(**kwargs)
        self.session.verify = verify
        self.session.headers.update(
            {
                'Accept': 'application/json',
                'User-Agent': default_user_agent(),
                'Authorization': f'token {token}',
            }
        )

    def get_organization_by_name(self, org):
        url = f'{self.base_url}/orgs/{org}'
        return self.get_json(url)

    def get_all_users(self, org):
        url = f'{self.base_url}/orgs/{org}/members'
        return (self.get_json(m['url']) for m in self.get_all_pages(url))

    def get_all_repos(self, org):
        url = f'{self.base_url}/orgs/{org}/repos'
        result = (self.get_json(m['url']) for m in self.get_all_pages(url))
        return result

    def get_branches(self, full_repo):
        url = f'{self.base_url}/repos/{full_repo}/branches'
        return self.get_all_pages(url)

    def get_commits(self, full_repo, sha, since, until):
        url = f'{self.base_url}/repos/{full_repo}/commits?sha={sha}&since={since.isoformat()}&until={until.isoformat()}'
        return self.get_all_pages(url)

    def get_pullrequests(self, full_repo):
        url = f'{self.base_url}/repos/{full_repo}/pulls?state=all&sort=updated&direction=desc'
        return (self.get_json(m['url']) for m in self.get_all_pages(url))

    def get_pr_comments(self, full_repo, pr_id):
        url = f'{self.base_url}/repos/{full_repo}/pulls/{pr_id}/comments'
        return self.get_all_pages(url)

    def get_pr_reviews(self, full_repo, pr_id):
        url = f'{self.base_url}/repos/{full_repo}/pulls/{pr_id}/reviews'
        return self.get_all_pages(url)

    def get_pr_commits(self, full_repo, pr_id):
        url = f'{self.base_url}/repos/{full_repo}/pulls/{pr_id}/commits'
        return self.get_all_pages(url)

    # Raw web service operations with optional rate limiting
    def get_json(self, url):
        return self.get_raw_result(url).json()

    def get_raw_result(self, url):
        # retry if rate-limited
        max_retries = 3
        for i in range(1, max_retries + 1):
            try:
                result = self.session.get(url)
                result.raise_for_status()
                return result
            except requests.exceptions.HTTPError as e:
                if e.response.headers['X-RateLimit-Remaining'] == '0' and i < max_retries:
                    # rate-limited!  Sleep until it's oK, then try again
                    reset_time = datetime.fromtimestamp(
                        int(e.response.headers['X-RateLimit-Reset']), pytz.utc
                    )
                    now = datetime.utcnow().replace(tzinfo=pytz.utc)
                    reset_wait = reset_time - now
                    logger.warn(
                        f'Github rate limit exceeded.  Trying again in {str(reset_wait)}...'
                    )
                    time.sleep(reset_wait.seconds)
                    continue

                raise

    # Handle pagination
    def get_all_pages(self, url):
        current_page_values = deque()
        while True:
            # current page is exhausted; get a new page if there is one
            if not current_page_values:
                if not url:
                    return  # no next page

                # fetch the next page
                result = self.get_raw_result(url)
                page = result.json()
                if type(page) != list:
                    raise ValueError(f'Expected an array of json results, but got: {page}')

                if len(page) == 0:
                    return  # no new values returned

                current_page_values.extend(page)
                url = result.links['next']['url'] if 'next' in result.links else None

            yield current_page_values.popleft()
