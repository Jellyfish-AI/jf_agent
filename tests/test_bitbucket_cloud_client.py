import os
from datetime import datetime, timedelta

import requests_mock
import json
from unittest import TestCase

from jf_agent.git.bitbucket_cloud_client import BitbucketCloudClient
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
