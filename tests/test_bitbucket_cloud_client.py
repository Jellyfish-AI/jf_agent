import json
import os
import re
from datetime import datetime, timedelta
from unittest import TestCase

import requests_mock

from jf_agent.git.bitbucket_cloud_client import (
    MAX_REPO_QUERY_URL_LENGTH,
    BitbucketCloudClient,
)
from jf_agent.session import retry_session

URI = 'https://bitbucket.testco.com'
TEST_INPUT_FILE_PATH = 'test_data/bitbucket_cloud/'


def get_connection():
    mock_server_info_resp = (
        '{"baseUrl":"' + URI + '","version":"1001.0.0-SNAPSHOT",'
        '"versionNumbers":[1001,0,0],"deploymentType":"Cloud","buildNumber":100218,'
        '"buildDate":"2023-03-16T08:21:48.000-0400","serverTime":"2023-03-17T16:32:45.255-0400",'
        '"defaultLocale":{"locale":"en_US"}} '
    )

    username = 'username'
    password = 'password'  # pragma: allowlist secret

    # https://test-co.atlassian.net/rest/api/2/serverInfo
    with requests_mock.Mocker() as m:
        m.register_uri(
            'GET',
            'https://test-co.atlassian.net/rest/api/2/serverInfo',
            text=f'{mock_server_info_resp}',
        )
        bbc_client = BitbucketCloudClient(URI, username, password, retry_session())

    return bbc_client


def _get_test_data(file_name):
    print(os.listdir(f"{os.path.dirname(__file__)}"))
    with open(f"{os.path.dirname(__file__)}/{TEST_INPUT_FILE_PATH}{file_name}", "r") as f:
        return f.read()


class TestBitbucketCloudClient(TestCase):

    faux_ratelimit_timestamp = datetime.now()
    faux_ratelimit_wait_time = 10
    faux_ratelimit_try = 0
    mock_response = "valid bitbucket cloud data placeholder"
    bitbucket_connection = None

    # emulate a server response asking us to back off
    def ratelimited_callback(self, request, context):
        request_time = datetime.now()
        is_timeboxed = (request_time - self.faux_ratelimit_timestamp) < timedelta(
            seconds=self.faux_ratelimit_wait_time
        )
        if is_timeboxed:
            self.faux_ratelimit_try += 1
        else:
            self.faux_ratelimit_timestamp = request_time
            self.faux_ratelimit_try = 1
        if self.faux_ratelimit_try > 3:
            context.headers['Retry-After'] = str(self.faux_ratelimit_wait_time)
            context.status_code = 429
            return "429 - Too many requests"
        context.status_code = 200
        return self.mock_response  # send back normal status and create a new timestamp

    @classmethod
    def setUpClass(cls):
        cls.bitbucket_connection = get_connection()
        cls.mock_response = _get_test_data('test_repos.json')

    def test_download_with_429_timeout(self):
        with requests_mock.Mocker() as m:
            m.register_uri('GET', f'{URI}', text=self.ratelimited_callback)
            for i in range(0, 3):  # quickly exhaust our fake ratelimit
                results = self.bitbucket_connection.get_raw_result(URI)
                print(f"{i} -- {datetime.now()} -- {results}")
            request_time = datetime.now()
            results = self.bitbucket_connection.get_raw_result(
                URI
            )  # hit 429, wait, get results delayed
            return_time = datetime.now()
            self.assertGreaterEqual(
                (return_time - request_time).total_seconds(), self.faux_ratelimit_wait_time
            )
            json_response = json.loads(results.text)
            self.assertGreaterEqual(
                len(json_response[0]), 19
            )  # number of elements in test repo json (2023-05-26)

    def test_get_all_repos_requests_max_page_size_and_follows_next(self):
        first_page = f'{URI}/2.0/repositories/test-ws?role=MEMBER&pagelen=100'
        second_page = f'{URI}/2.0/repositories/test-ws?role=MEMBER&pagelen=100&page=2'
        with requests_mock.Mocker() as m:
            m.register_uri(
                'GET',
                first_page,
                complete_qs=True,
                json={'values': [{'name': 'a'}, {'name': 'b'}], 'next': second_page},
            )
            m.register_uri('GET', second_page, complete_qs=True, json={'values': [{'name': 'c'}]})

            repos = list(self.bitbucket_connection.get_all_repos('test-ws'))

            self.assertEqual([r['name'] for r in repos], ['a', 'b', 'c'])
            self.assertEqual(m.call_count, 2)


class TestBitbucketCloudRepoAllowlistQuery(TestCase):
    """Cover the BBQL `q` filter that narrows the repo listing to the allowlist."""

    REPOS_URL = f'{URI}/2.0/repositories/test-ws'
    UNFILTERED_URL = f'{REPOS_URL}?role=MEMBER&pagelen=100'

    def setUp(self):
        self.client = get_connection()

    @staticmethod
    def _queries(mocker):
        return [m.qs.get('q', [None])[0] for m in mocker.request_history]

    def test_allowlist_is_queried_as_a_contains_match(self):
        """An exact match is case sensitive, so it would drop a repo the local filter keeps."""
        with requests_mock.Mocker() as m:
            m.register_uri(
                'GET',
                f'{self.UNFILTERED_URL}&q=(name~"repo-a" OR name~"repo-b")',
                complete_qs=True,
                json={'values': [{'uuid': '{1}', 'name': 'repo-a'}]},
            )

            repos = list(self.client.get_all_repos('test-ws', ['repo-a', 'repo-b']))

        self.assertEqual([r['name'] for r in repos], ['repo-a'])
        self.assertNotIn('name=', self._queries(m)[0])

    def test_every_allowlist_entry_lands_in_a_query(self):
        """An entry that no query carries is a repo the agent never lists and silently loses."""
        allowlist = [f'repo-{i}-{"n" * (i % 400)}' for i in range(250)]
        with requests_mock.Mocker() as m:
            m.register_uri('GET', self.REPOS_URL, json={'values': []})

            list(self.client.get_all_repos('test-ws', allowlist))

        queried = set()
        for request in m.request_history:
            self.assertLessEqual(len(request.url), MAX_REPO_QUERY_URL_LENGTH)
            queried.update(re.findall(r'name~"([^"]*)"', request.qs['q'][0]))

        self.assertGreater(len(m.request_history), 1)
        self.assertEqual(queried, set(allowlist))

    def test_repo_matched_by_two_batches_is_yielded_once(self):
        with requests_mock.Mocker() as m:
            m.register_uri(
                'GET',
                self.REPOS_URL,
                json={'values': [{'uuid': '{dupe}', 'name': 'shared'}]},
            )

            repos = list(self.client.get_all_repos('test-ws', ['shared'] * 150))

        self.assertEqual([r['uuid'] for r in repos], ['{dupe}'])

    def test_uuid_in_the_allowlist_lists_unfiltered(self):
        """BBQL cannot match a uuid case insensitively, so a query would drop the repo."""
        with requests_mock.Mocker() as m:
            m.register_uri('GET', self.UNFILTERED_URL, complete_qs=True, json={'values': []})

            list(
                self.client.get_all_repos(
                    'test-ws', ['repo-a', '{12345678-1234-1234-1234-123456789ABC}']
                )
            )

        self.assertEqual(self._queries(m), [None])

    def test_rejected_query_falls_back_to_the_unfiltered_listing(self):
        """A workspace that refuses the query must still ingest every repo."""
        with requests_mock.Mocker() as m:
            m.register_uri('GET', self.REPOS_URL, status_code=400)
            m.register_uri(
                'GET',
                self.UNFILTERED_URL,
                complete_qs=True,
                json={'values': [{'uuid': '{1}', 'name': 'repo-a'}]},
            )

            repos = list(self.client.get_all_repos('test-ws', ['repo-a']))

        self.assertEqual([r['name'] for r in repos], ['repo-a'])
        self.assertIsNotNone(self._queries(m)[0])
        self.assertIsNone(self._queries(m)[-1])
