from dataclasses import dataclass
from functools import partial
import logging
from typing import Callable, Generator
from jf_agent.jf_jira import get_basic_jira_connection
from jf_agent.jf_jira.jira_download import download_users
from jira import JIRAError


logger = logging.getLogger(__name__)


@dataclass
class JiraCloudManifestAdapter:
    def __init__(self, config, creds) -> None:
        self.jira_connection = get_basic_jira_connection(config, creds)
        self.config = config

    def _get_all_pages(self, url_path: str, page_size: int = 50) -> list:
        curr = 0
        all_items = []
        while True:
            page = self.jira_connection._get_json(
                url_path, {'startAt': curr, "maxResults": page_size}
            )
            all_items += page
            curr += page_size

            if len(page) == 0:
                break
        return all_items

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

    def get_projects_count(self) -> int:
        return len(self.jira_connection.projects())

    def get_project_versions_count(self) -> int:
        return sum(
            [len(self.jira_connection.project_versions(p)) for p in self.jira_connection.projects()]
        )

    def get_boards_count(self) -> int:
        return len([b for b in self._func_paginator(func_endpoint=self.jira_connection.boards)])

    def get_sprints_count(self) -> int:
        total_sprints = 0
        for board in self._func_paginator(func_endpoint=self.jira_connection.boards):
            bound_sprints_func = partial(self.jira_connection.sprints, board.id)
            try:
                total_sprints += len([s for s in self._func_paginator(bound_sprints_func)])
            except JIRAError as e:
                # From what I can tell, there isn't an easy way to tell
                # from the 'board' object if sprints are enabled or not
                if e.status_code == 400 and e.text == 'The board does not support sprints':
                    continue
                else:
                    raise
        return total_sprints

    def get_issues_count(self) -> int:
        # Query for all issues via JQL, but ask for 0 results.
        # Meta data is returned that will give us the total number of
        # (unpulled) results
        result = self._get_jql_search(jql_search="", max_results=0)
        return result['total']

    def get_issues_data_count(self) -> int:
        return self.get_issues_count()
