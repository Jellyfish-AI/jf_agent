from collections import deque
from datetime import datetime, timedelta
import logging
import pytz
import requests
from requests import HTTPError, Response
from requests.utils import default_user_agent
import time

from jf_agent.session import retry_session
from jf_ingest import logging_helper

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
                'User-Agent': f'jellyfish/1.0 ({default_user_agent()})',
                'Authorization': f'token {token}',
            }
        )

    def get_scopes_of_api_token(self):
        # Make an empty call against the orgs API to be quick
        # and get the OAuth scopes
        url = f'{self.base_url}/orgs/'
        result = self.session.get(url)
        return result.headers.get('X-OAuth-Scopes')

    def get_organization_by_name(self, org):
        url = f'{self.base_url}/orgs/{org}'
        return self.get_json(url)

    def get_all_users(self, org):
        url = f'{self.base_url}/orgs/{org}/members'
        for m in self.get_all_pages(url):
            try:
                yield self.get_json(m['url'])
            except requests.exceptions.HTTPError as e:
                # non-403 and non-404 should bubble up
                if e.response.status_code not in (403, 404):
                    raise

                logging_helper.log_standard_error(
                    logging.WARNING, msg_args=[m["url"], e.response.status_code], error_code=3061,
                )

    def get_all_repos(self, org):
        url = f'{self.base_url}/orgs/{org}/repos'
        for m in self.get_all_pages(url):
            try:
                yield self.get_json(m['url'])
            except requests.exceptions.HTTPError as e:
                # non-403 should bubble up
                if e.response.status_code != 403:
                    raise

                # we've seen some strange behavior with ghe, where we can get a 403 for
                # a repo that comes back in the list.  SKip them.
                logging_helper.log_standard_error(
                    logging.WARNING, msg_args=[m["url"]], error_code=3081,
                )

    def get_branches(self, full_repo):
        url = f'{self.base_url}/repos/{full_repo}/branches'
        return self.get_all_pages(url)

    def get_commits(self, full_repo, sha, since, until):
        url = f'{self.base_url}/repos/{full_repo}/commits?sha={sha}'
        if since:
            url += f'&since={since.isoformat()}'
        if until:
            url += f'&until={until.isoformat()}'
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

    def get_commit_by_ref(self, full_repo_name, ref):
        url = f'{self.base_url}/repos/{full_repo_name}/commits/{ref}'
        try:
            raw = self.get_raw_result(url)
            return raw.json()
        except HTTPError as e:
            if e.response.status_code in (422,):
                logging_helper.log_standard_error(
                    logging.WARNING,
                    msg_args=[e.response.status_code, ref, full_repo_name],
                    error_code=3121,
                )
                return None

    # Raw web service operations with optional rate limiting
    def get_json(self, url):
        return self.get_raw_result(url).json()

    def get_raw_result(self, url):
        # retry if rate-limited
        max_retries = 5
        for i in range(1, max_retries + 1):
            try:
                result = self.session.get(url)

                # HACK: This appears to happen after we have been
                # rate-limited when hitting certain URLs, there is
                # likely a more elegant way to solve this but it takes
                # about an hour to test each time and it works.
                if result.status_code == 403:
                    result = self.session.get(url)

                result.raise_for_status()
                return result
            except requests.exceptions.HTTPError as e:
                remaining_ratelimit = e.response.headers.get('X-RateLimit-Remaining')
                ratelimit_reset = e.response.headers.get('X-RateLimit-Reset')

                if remaining_ratelimit != '0':
                    # We hit a non-rate-limiting-related error.  Don't retry
                    raise

                if i >= max_retries:
                    logging_helper.log_standard_error(
                        logging.ERROR, msg_args=[url, i], error_code=3101,
                    )
                    raise

                # rate-limited!  Sleep until it's ok, then try again
                reset_time = datetime.fromtimestamp(int(ratelimit_reset), pytz.utc)
                now = datetime.utcnow().replace(tzinfo=pytz.utc)
                reset_wait = reset_time - now

                reset_wait_in_seconds = reset_wait.total_seconds()

                # Sometimes github gives a reset time in the
                # past. In that case, wait for 5 mins just in case.
                if reset_wait_in_seconds <= 0:
                    reset_wait_in_seconds = 300

                # Sometimes github gives a reset time way in the
                # future. But rate limits reset each hour, so don't
                # wait longer than that
                reset_wait_in_seconds = min(reset_wait_in_seconds, 3600)
                reset_wait_str = str(timedelta(seconds=reset_wait_in_seconds))
                logging_helper.log_standard_error(
                    logging.WARNING, msg_args=[reset_wait_str], error_code=3091,
                )
                # often the GH reset time is off by <1 second, causing another rate-limit. 2 seconds buffer added.
                time.sleep(reset_wait_in_seconds + 2)
                continue  # retry

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
