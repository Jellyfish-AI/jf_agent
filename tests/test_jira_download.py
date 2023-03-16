import json
from requests import HTTPError
from unittest.mock import MagicMock
from mock import patch
import unittest
from unittest import TestCase
import requests_mock
import os
import json

from jf_agent.jf_jira.jira_download import get_issues
from jf_agent.jf_jira import get_basic_jira_connection


def get_connection():
    class PartialConfig():
        jira_url = 'https://jelly-ai.atlassian.net/'
        skip_ssl_verification = False

    config = PartialConfig()

    class PartialCreds():
        jira_password = None
        jira_username = None
        jira_bearer_token = ''

    creds = PartialCreds()
    creds.jira_bearer_token = os.environ.get('JIRA_TOKEN')

    return get_basic_jira_connection(config, creds)


class TestJiraDownload(TestCase):

    URL_TEMPLATE_START = '?jql=project+in+%28%22OJ%22%29+and+updatedDate+%3E+0+order+by+id+asc&startAt='
    URL_TEMPLATE_END = '&validateQuery=True&fields=updated&maxResults=10'
    ISSUE_JQL = 'project in ("OJ") and updatedDate > 0'
    START_AT = 0

    with open("test_data/jira/test_issues_response.json", "r") as issues_file:
        mock_response = issues_file.read()


    def setUp(self) -> None:
        return None


    def test_get_issues_once(self):
        jira_connection = get_connection()
        response_value = self.mock_response

        with requests_mock.Mocker() as m:
            m.register_uri('GET',
                           f'https://jelly-ai.atlassian.net/rest/api/2/search'
                           f'{self.URL_TEMPLATE_START}{self.START_AT}{self.URL_TEMPLATE_END}',
                           text=response_value)

            issues = get_issues(jira_connection, issue_jql=self.ISSUE_JQL, start_at=0, batch_size=10)

            self.assertGreaterEqual(len(issues), 20)





if __name__ == "__main__":
    unittest.main()
