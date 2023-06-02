import json

from dataclasses import dataclass
from functools import partial
from typing import Callable, Generator
from jf_agent.jf_jira import get_basic_jira_connection
from jf_agent.jf_jira.jira_download import download_users
from jira import JIRAError


@dataclass
class JiraCloudManifestAdapter:
    def __init__(self, config, creds) -> None:
        self.jira_connection = get_basic_jira_connection(config, creds)
        self.config = config
        self.jira_url = config.jira_url

    def _get_jql_search(self, jql_search: str, max_results: int = 0):
        return self.jira_connection._get_json(
            'search', {'jql': jql_search, "maxResults": max_results}
        )

    def _func_paginator(
        self, func_endpoint: Callable, page_size: int = 50
    ) -> Generator[dict, None, None]:
        page_size = 50
        curs = 0
        while True:
            page = func_endpoint(startAt=curs, maxResults=page_size)
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
        return len(self.jira_connection.fields())

    def get_resolutions_count(self) -> int:
        # Lazy loading paranoia, we might not need to do this for loop
        return len(self.jira_connection.resolutions())

    def get_issue_types_count(self) -> int:
        # Lazy loading paranoia, we might not need to do this for loop
        return len(self.jira_connection.issue_types())

    def get_issue_link_types_count(self) -> int:
        return len(self.jira_connection.issue_link_types())

    def get_priorities_count(self) -> int:
        return len(self.jira_connection.priorities())

    def _get_projects(self) -> Generator[dict, None, None]:
        for project in self._page_get_results(
            url=f'{self.jira_url}/rest/api/latest/project/search?startAt=%s&maxResults=500&status=archived&status=live'
        ):
            yield project

    def get_projects_count(self) -> int:
        url = f'{self.jira_url}/rest/api/latest/project/search?startAt=0&maxResults=0&status=archived&status=live'
        result = self._get_raw_result(url=url)
        return result['total']

    def get_project_keys(self) -> list[str]:
        return [project['key'] for project in self._get_projects()]

    def get_project_versions_count(self) -> int:
        total_project_versions = 0
        for project in self._get_projects():
            total_project_versions += self._get_raw_result(
                url=f'{self.jira_url}/rest/api/latest/project/{project["id"]}/version?startAt=0&maxResults=0&state=future,active,closed'
            )['total']

        return total_project_versions

    def get_boards_count(self) -> int:
        return len([b for b in self._func_paginator(func_endpoint=self.jira_connection.boards)])

    def get_sprints_count(self) -> int:
        def _get_sprints_for_board(board_id: int):
            total_sprints_for_board = 0
            try:
                total_sprints_for_board += len(
                    [
                        sprint
                        for sprint in self._page_get_results(
                            url=f'{self.jira_url}/rest/agile/1.0/board/{board_id}/sprint?startAt=%s&maxResults=100'
                        )
                    ]
                )
            except JIRAError as e:
                # From what I can tell, there isn't an easy way to tell
                # from the 'board' object if sprints are enabled or not
                if e.status_code == 400 and (
                    e.text == 'The board does not support sprints'
                    or e.text == 'The board doesn\'t support sprints.'
                ):
                    print(f"Couldn't get sprints for board {board_id}")
                elif (
                    e.status_code == 500
                    and e.text == 'This board has no columns with a mapped status.'
                ):
                    print(f"Board {board_id} doesn't support sprints -- skipping")
                else:
                    raise

            return total_sprints_for_board

        total_sprints_across_all_boards = 0
        for board in self._page_get_results(
            url=f'{self.jira_url}/rest/agile/1.0/board?startAt=%s&maxResults=100'
        ):
            total_sprints_across_all_boards += _get_sprints_for_board(board["id"])

        return total_sprints_across_all_boards

    def get_issues_count(self) -> int:
        # Query for all issues via JQL, but ask for 0 results.
        # Meta data is returned that will give us the total number of
        # (unpulled) results
        result = self._get_jql_search(jql_search="", max_results=0)
        return result['total']

    def get_issues_data_count(self) -> int:
        return self.get_issues_count()

    def _get_raw_result(self, url) -> dict:
        response = self.jira_connection._session.get(url)
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
