import json
import logging
from dataclasses import dataclass
import traceback
from typing import Callable, Generator
from jf_agent.jf_jira import get_basic_jira_connection
from jf_agent.jf_jira.jira_download import download_users
from jira import JIRAError

from jf_ingest import logging_helper
from jf_ingest.utils import retry_for_status

logger = logging.getLogger(__name__)


@dataclass
class JiraCloudManifestAdapter:
    def __init__(self, company_slug, config, creds) -> None:
        self.company_slug = company_slug
        self.jira_connection = get_basic_jira_connection(config, creds)
        self.config = config

        # Sanity check: remove trailing slash because
        # it doesn't play nice with JIRA API endpoints
        self.jira_url = config.jira_url[:-1] if config.jira_url.endswith('/') else config.jira_url

        self._boards_cache = []
        self._projects_cache = []

        # Set up caches
        self._get_all_boards()
        self._get_all_projects()

    def _func_paginator(
        self, func_endpoint: Callable, page_size: int = 50
    ) -> Generator[dict, None, None]:
        page_size = 50
        curs = 0
        while True:
            page = retry_for_status(func_endpoint, startAt=curs, maxResults=page_size)
            page_length = len(page)
            # Check if we are at the end of all pages
            if page_length == 0:
                break
            for entry in page:
                yield entry
            curs += page_length

    def get_users_count(self) -> int:
        # Getting user data is complicated. For this use case,
        # run the specialized jira_downloader logic and grab the count
        try:
            jira_users = download_users(
                self.jira_connection,
                self.config.jira_gdpr_active,
                required_email_domains=self.config.jira_required_email_domains,
                is_email_required=self.config.jira_is_email_required,
                quiet=True,
            )
            return len(jira_users)
        except RuntimeError as e:
            return 0

    def get_fields_count(self) -> int:
        # Lazy loading paranoia, we might not need to do this for loop
        return len(retry_for_status(self.jira_connection.fields))

    def get_resolutions_count(self) -> int:
        # Lazy loading paranoia, we might not need to do this for loop
        return len(retry_for_status(self.jira_connection.resolutions))

    def get_issue_types_count(self) -> int:
        # Lazy loading paranoia, we might not need to do this for loop
        return len(retry_for_status(self.jira_connection.issue_types))

    def get_issue_link_types_count(self) -> int:
        return len(retry_for_status(self.jira_connection.issue_link_types))

    def get_priorities_count(self) -> int:
        return len(retry_for_status(self.jira_connection.priorities))

    def _get_all_projects(self) -> list[dict]:
        if not self._projects_cache:
            if self.config.jira_gdpr_active:
                all_projects = [
                    project
                    for project in self._page_get_results(
                        url=f'{self.jira_url}/rest/api/latest/project/search?startAt=%s&maxResults=500&status=archived&status=live'
                    )
                ]
            # Different endpoint for Jira Server
            else:
                all_projects = [
                    project
                    for project in self._get_raw_result(
                        url=f'{self.jira_url}/rest/api/latest/project?includedArchived=True'
                    )
                ]

            # Filter for only projects that are included in the config
            self._projects_cache = [
                project
                for project in all_projects
                if project['key'] in self.config.jira_include_projects
            ]

        return self._projects_cache

    def get_project_data_dicts(self) -> list[dict]:
        return [
            {"key": project['key'], "id": project['id']} for project in self._get_all_projects()
        ]

    def get_project_versions_count_for_project(self, project_id: int) -> int:
        return self._get_raw_result(
            url=f'{self.jira_url}/rest/api/latest/project/{project_id}/version?startAt=0&maxResults=0&state=future,active,closed'
        )['total']

    def get_issues_count(self) -> int:
        # Query for all issues via JQL, but ask for 0 results.
        # Meta data is returned that will give us the total number of
        # (unpulled) results
        result = self._get_jql_search(jql_search="", max_results=0)
        return result['total']

    def _get_all_boards(self):
        if not self._boards_cache:
            self._boards_cache = [
                board
                for board in self._page_get_results(
                    url=f'{self.jira_url}/rest/agile/1.0/board?startAt=%s&maxResults=100'
                )
            ]

        return self._boards_cache

    def get_project_versions_count(self) -> int:
        total_project_versions = 0
        for project in self._get_all_projects():
            total_project_versions += self._get_raw_result(
                url=f'{self.jira_url}/rest/api/latest/project/{project["id"]}/version?startAt=0&maxResults=0&state=future,active,closed'
            )['total']

        return total_project_versions

    def get_boards_count(self) -> int:
        return len([b for b in self._func_paginator(func_endpoint=self.jira_connection.boards)])

    def test_basic_auth_for_project(self, project_id: int) -> bool:
        # Doing a basic query for issues is the best way to test auth.
        # Catch and error, if it happens, and bubble up more specific error
        try:
            self.get_issues_count_for_project(project_id=project_id)
            return True
        except JIRAError:
            return False
        except Exception as e:
            # This is unexpected behavior and it should never happen, log the error
            # before returning
            logging_helper.send_to_agent_log_file(
                'Unusual exception encountered when testing auth. '
                'This should not affect agent uploading. '
                f'JIRAError was expected but the following error was raised: {e}',
                level=logging.ERROR,
            )
            logging_helper.send_to_agent_log_file(traceback.format_exc(), level=logging.ERROR)
            return False

    def get_issues_count_for_project(self, project_id: int) -> int:
        return self._get_jql_search(jql_search=f'project = "{project_id}"', max_results=0)['total']

    def _get_raw_result(self, url) -> dict:
        response = retry_for_status(self.jira_connection._session.get, url)
        response.raise_for_status()
        json_str = response.content.decode()
        return json.loads(json_str)

    def _page_get_results(self, url: str):
        start_at = 0
        while True:
            page_result = self._get_raw_result(url % start_at)
            for value in page_result['values']:
                yield value

            if page_result['isLast']:
                break
            else:
                start_at += len(page_result['values'])

    def _get_jql_search(self, jql_search: str, max_results: int = 0):
        return retry_for_status(
            self.jira_connection._get_json, 'search', {'jql': jql_search, "maxResults": max_results}
        )
