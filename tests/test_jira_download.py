import os
import requests_mock
import time
import json
from unittest import TestCase
from jira.resources import Issue as JiraIssue

from jf_agent.jf_jira.jira_download import get_issues, download_all_issue_metadata
from tests.utils import get_connection


class TestJiraDownload(TestCase):
    URL_BASE = 'https://test-co.atlassian.net/rest/api/2/search'
    URL_TEMPLATE_START = (
        '?jql=project+in+%28%22TC%22%29+and+updatedDate+%3E+0+order+by+id+asc&startAt='
    )
    URL_TEMPLATE_END = '&validateQuery=True&fields=updated&maxResults='
    ISSUE_JQL = 'project in ("TC") and updatedDate > 0'
    HTTP_500_JSON = '{"errorMessages":["Internal server error"],"errors":{}}'

    start_at = 0
    max_results = 100

    jira_connection = None
    mock_response = None

    # emulate the effect of there being 100 possible issues to return,
    # with the issue at position 50 being malformed and any requests
    # that would return it resulting in an HTTP 500 response instead
    def one_bad_issue_callback(self, request, context):
        bad_issue_json = {
            "expand": "operations,versionedRepresentations,editmeta,changelog,renderedFields",
            "id": "9999",
            "self": "https://test-co.atlassian.net/rest/api/2/issue/9999",
            "key": "TC-BAD",
            "fields": {"updated": "2022-12-08T18:43:33.703-0500"},
        }
        json_response = json.loads(self.mock_response)
        json_response['issues'].insert(50, bad_issue_json)

        start_at = int(str(request).split("&startAt=")[1].split("&")[0])
        batch_size = int(str(request).split("&maxResults=")[1])
        end_at = start_at + batch_size
        issue_batch = json_response['issues'][start_at:end_at]

        # is this batched request going to have the bad issue contained within?
        if bad_issue_json in issue_batch:
            context.status_code = 500
            context.reason = "Internal server error"
            return ""
        else:
            json_response['issues'] = issue_batch
            context.status_code = 200
            return json.dumps(json_response)

    @classmethod
    def setUpClass(cls):
        cls.jira_connection = get_connection()
        with open(
            f"{os.path.dirname(__file__)}/test_data/jira/test_issues_response.json", "r"
        ) as issues_file:
            issue_json = issues_file.read()
        cls.mock_response = issue_json

    def test_get_issues_once(self):
        with requests_mock.Mocker() as m:
            m.register_uri(
                'GET',
                f'{self.URL_BASE}{self.URL_TEMPLATE_START}'
                f'{self.start_at}{self.URL_TEMPLATE_END}{self.max_results}',
                text=self.mock_response,
            )
            time.sleep(
                0.5
            )  # sometimes there's a split second before the registration is fully in effect
            issues = get_issues(
                self.jira_connection, issue_jql=self.ISSUE_JQL, start_at=0, batch_size=100
            )

        self.assertEqual(len(issues), 100)
        self.assertTrue(all(isinstance(issue, JiraIssue) for issue in issues))

    def test_download_all_issue_metadata_with_server_error(self):
        project_ids = ["TC"]
        earliest_issue_dt = ''
        num_parallel_threads = 1
        issue_filter = ''

        with requests_mock.Mocker() as m:
            # the urls hit during the first pass
            # batch size starts at 1000 and gets halved until 0
            m.register_uri('GET', f'{self.URL_BASE}', text=self.one_bad_issue_callback)
            issue_metadata = download_all_issue_metadata(
                self.jira_connection,
                project_ids,
                earliest_issue_dt,
                num_parallel_threads,
                issue_filter,
            )

        self.assertGreaterEqual(len(issue_metadata), 1)
