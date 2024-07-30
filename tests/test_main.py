import os
import requests_mock
import time
import json
from unittest import TestCase
from jira.resources import Issue as JiraIssue

from jf_agent.jf_jira.jira_download import get_issues, download_all_issue_metadata
from jf_agent.main import download_data
from tests.utils import get_connection


class TestJiraDownload(TestCase):

    start_at = 0
    max_results = 100

    jira_connection = None
    mock_response = None

    @classmethod
    def setUpClass(cls):
        cls.jira_connection = get_connection()
        with open(
            f"{os.path.dirname(__file__)}/test_data/jira/test_issues_response.json", "r"
        ) as issues_file:
            issue_json = issues_file.read()
        cls.mock_response = issue_json

    def test_download_data_without_jira_config(self):
        """
        Tests that download_data runs successfully without a jira_config
        """

        class PartialConfig:
            jira_url = 'https://test-co.atlassian.net/'
            skip_ssl_verification = False
            git_configs = []

        class PartialIngestConfig:
            jira_config = None

        class PartialCreds:
            jira_password = None
            jira_username = None
            jira_bearer_token = 'asdf'

        config = PartialConfig()
        creds = PartialCreds()
        ingest_config = PartialIngestConfig()

        statuses = download_data(
            config,
            creds,
            ingest_config=ingest_config,
            endpoint_jira_info={},
            endpoint_git_instances_info=None,
            jf_options=None
        )

        self.assertEqual(statuses, [])