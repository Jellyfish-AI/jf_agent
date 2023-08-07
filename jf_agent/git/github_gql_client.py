import inspect
import logging
from datetime import datetime
from requests import Session
from typing import Generator

from jf_agent.git.github_gql_utils import (
    get_github_gql_base_url,
    get_github_gql_session,
    get_raw_result,
    page_results,
)
from jf_agent.git.utils import log_and_print_request_error

logger = logging.getLogger(__name__)


class GithubGqlClient:

    GITHUB_GQL_USER_FRAGMENT = "... on User {id, login, email, name }"
    GITHUB_GQL_PAGE_INFO_BLOCK = "pageInfo {hasNextPage, endCursor}"
    GITHUB_GQL_COMMIT_FRAGMENT = f"""
        ... on Commit {{
            sha: oid
            url
            author {{
                user {{
                    {GITHUB_GQL_USER_FRAGMENT}
                }}
            }}
            message
            committedDate
            authoredDate
            parents {{totalCount}}
        }}
    """
    GITHUB_GQL_SHORT_REPO_FRAGMENT = "... on Repository {name, id, url}"

    # The PR query is HUGE, we shouldn't query more than about 25 at a time
    MAX_PAGE_SIZE_FOR_PR_QUERY = 25

    def __init__(self, base_url: str, token: str, verify: bool, session: Session) -> None:
        self.token = token
        self.base_url = get_github_gql_base_url(base_url=base_url)

        self.session = get_github_gql_session(token=token, verify=verify, session=session)

    def _get_raw_result(self, query_body: str):
        return get_raw_result(query_body=query_body, base_url=self.base_url, session=self.session)

    def _page_results(self, query_body: str, path_to_page_info: str):
        return page_results(
            query_body=query_body,
            path_to_page_info=path_to_page_info,
            session=self.session,
            base_url=self.base_url,
        )

    @staticmethod
    def _to_git_timestamp(timestamp: datetime):
        return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_organization_by_login(self, login: str):
        query_body = f"""{{
            organization(login: \"{login}\") {{
                id
                login
                name
                url
            }}
        }}
        """
        try:
            return self._get_raw_result(query_body=query_body)['data']['organization']
        except Exception as e:
            log_and_print_request_error(
                e, f'fetching source project {login}. ' f'Skipping...',
            )
            return None

    def get_users(self, login: str) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: \"{login}\") {{
                userQuery: membersWithRole(first: 100, after: %s) {{
                    {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                    users: nodes {{
                        {self.GITHUB_GQL_USER_FRAGMENT}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.userQuery'
        ):
            for user in page['data']['organization']['userQuery']['users']:
                yield user

    def get_repos(
        self, login: str, repo_filters: list[filter] = None
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                # THIS IS NEEDED BY NORMALIZE_PROJECT
                id
                login
                url
                repoQuery: repositories(first: 50, after: %s) {{
                    {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                    repos: nodes {{
                        ... on Repository {{
                            id: databaseId
                            name
                            fullName: nameWithOwner
                            url
                            isFork
                            defaultBranch: defaultBranchRef {{
                                    name
                            }}
                            # This should be broken out into a separate query if 
                            # hasNextPage is True.
                            branchQuery: refs(refPrefix:"refs/heads/", first: 50) {{
                                {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                                branches: nodes {{
                                    ... on Ref {{
                                        name
                                        target {{sha: oid}}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repoQuery'
        ):
            for api_repo in page['data']['organization']['repoQuery']['repos']:
                # Skip over excluded or ignore non-included
                if not all(filt(api_repo) for filt in repo_filters):
                    continue
                else:
                    # Potentially process more branches
                    if api_repo['branchQuery']['pageInfo']['hasNextPage']:
                        api_repo['branches'] = self.get_branches(
                            login=login, repo_name=api_repo['name']
                        )
                    else:
                        api_repo['branches'] = api_repo['branchQuery']['branches']
                    yield api_repo

    def get_branches(self, login: str, repo_name: str) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repository(name: "{repo_name}") {{
                    ... on Repository {{
                        branchQuery: refs(refPrefix:"refs/heads/", first: 100, after: %s) {{
                            {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                            branches: nodes {{
                                ... on Ref {{
                                    name
                                    target {{sha: oid}}
                                }}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repository.branchQuery'
        ):
            for api_branch in page['data']['organization']['repository']['branchQuery']['branches']:
                yield api_branch

    def get_commits(
        self, login: str, repo_name: str, branch_name: str, since: datetime
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    ... on Repository {{
                        branch: ref(qualifiedName: "{branch_name}") {{
                            target {{
                                ... on Commit {{
                                    history(first: 100, since: "{self._to_git_timestamp(since)}", after: %s) {{
                                        {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                                        commits: nodes {{
                                            {self.GITHUB_GQL_COMMIT_FRAGMENT}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.branch.target.history'
        ):
            for api_commit in page['data']['organization']['repo']['branch']['target']['history'][
                'commits'
            ]:
                yield api_commit

    #
    # PR Queries are HUGE, so pull out reusable blocks (comments, reviews, commits, etc)
    #
    def _get_pr_comments_query_block(self, enable_paging: bool = False):
        return f"""
            commentsQuery: comments(first: 50{', after: %s' if enable_paging else ''}) {{
                {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                
                comments: nodes {{
                    ... on IssueComment {{
                        author {{
                            {self.GITHUB_GQL_USER_FRAGMENT}
                        }}
                        body
                        createdAt
                    }}
                }}
            }}
        """

    def _get_pr_reviews_query_block(self, enable_paging: bool = False):
        return f"""
            reviewsQuery: reviews(first: 50{', after: %s' if enable_paging else ''}) {{
                {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                
                reviews: nodes {{
                    ... on PullRequestReview {{
                        author {{
                            {self.GITHUB_GQL_USER_FRAGMENT}
                        }}
                        id
                        state
                    }}
                }}
            }}
        """

    def _get_pr_commits_query_block(self, enable_paging: bool = False):
        return f"""
            commitsQuery: commits(first: 50{', after: %s' if enable_paging else ''}) {{
                {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                
                commits: nodes {{
                    ... on PullRequestCommit {{
                        commit {{
                            {self.GITHUB_GQL_COMMIT_FRAGMENT}
                        }}
                    }}
                }}
            }}
        """

    # Generally, if we are running agent ingest daily, there aren't that many PRs
    # day to day (for most repos). This function takes this assumption into account and
    # is a cheap test to see if we should make a big query against PRs.
    # I.e. it helps with determining the page size for get_prs(),
    # as well as IF we should call get PRs at all!
    def get_pr_last_update_dates(
        self, login: str, repo_name: str, page_size: int = MAX_PAGE_SIZE_FOR_PR_QUERY
    ) -> list[dict]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    prQuery: pullRequests(first: {page_size}, orderBy: {{direction: DESC, field: UPDATED_AT}}, after: null) {{
                        {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                        prs: nodes {{
                            ... on PullRequest {{
                                updatedAt
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        return self._get_raw_result(query_body=query_body)['data']['organization']['repo'][
            'prQuery'
        ]['prs']

    # PR query is HUGE, see above GITHUB_GQL_PR_* blocks for reused code
    # page_size is optimally variable. Most companies only have a few PRs day to day,
    # but if we are back populating we may want the page size to be higher
    def get_prs(
        self, login: str, repo_name: str, page_size: int = MAX_PAGE_SIZE_FOR_PR_QUERY
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    prQuery: pullRequests(first: {page_size}, orderBy: {{direction: DESC, field: UPDATED_AT}}, after: %s) {{
                        {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                        prs: nodes {{
                            ... on PullRequest {{
                                id: databaseId
                                number
                                additions
                                deletions
                                changedFiles
                                state
                                merged
                                createdAt
                                updatedAt
                                mergedAt
                                closedAt
                                title
                                body
                                url
                                baseRef {{
                                    name
                                }}
                                headRef {{
                                    name
                                }}
                                baseRepository {{ {self.GITHUB_GQL_SHORT_REPO_FRAGMENT} }}
                                headRepository {{ {self.GITHUB_GQL_SHORT_REPO_FRAGMENT} }}
                                author {{
                                    {self.GITHUB_GQL_USER_FRAGMENT}
                                }}
                                mergedBy {{
                                    {self.GITHUB_GQL_USER_FRAGMENT}
                                }}
                                mergeCommit {{
                                    {self.GITHUB_GQL_COMMIT_FRAGMENT}
                                }}
                                {self._get_pr_comments_query_block(enable_paging=False)}
                                {self._get_pr_reviews_query_block(enable_paging=False)}
                                {self._get_pr_commits_query_block(enable_paging=False)}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.prQuery'
        ):
            for api_pr in page['data']['organization']['repo']['prQuery']['prs']:
                # Process and add related PR data (comments, reviews, commits)
                # This may require additional API calls
                pr_number = api_pr['number']
                api_pr['comments'] = (
                    self.get_pr_comments(login, repo_name, pr_number=pr_number)
                    if api_pr['commentsQuery']['pageInfo']['hasNextPage']
                    else api_pr['commentsQuery']['comments']
                )
                api_pr['reviews'] = (
                    self.get_pr_reviews(login, repo_name, pr_number=pr_number)
                    if api_pr['reviewsQuery']['pageInfo']['hasNextPage']
                    else api_pr['reviewsQuery']['reviews']
                )
                api_pr['commits'] = (
                    self.get_pr_commits(login, repo_name, pr_number=pr_number)
                    if api_pr['commitsQuery']['pageInfo']['hasNextPage']
                    else [commit['commit'] for commit in api_pr['commitsQuery']['commits']]
                )
                yield api_pr

    def get_pr_comments(
        self, login: str, repo_name: str, pr_number: int
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    pr: pullRequest(number: {pr_number}) {{
                        ... on PullRequest {{
                            {self._get_pr_comments_query_block(enable_paging=True)}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.pr.commentsQuery'
        ):
            for api_pr_comment in page['data']['organization']['repo']['pr']['commentsQuery'][
                'comments'
            ]:
                yield api_pr_comment

    def get_pr_reviews(
        self, login: str, repo_name: str, pr_number: int
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    pr: pullRequest(number: {pr_number}) {{
                        ... on PullRequest {{
                            {self._get_pr_reviews_query_block(enable_paging=True)}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.pr.reviewsQuery'
        ):
            for api_pr_review in page['data']['organization']['repo']['pr']['reviewsQuery'][
                'reviews'
            ]:
                yield api_pr_review

    def get_pr_commits(
        self, login: str, repo_name: str, pr_number: int
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    pr: pullRequest(number: {pr_number}) {{
                        ... on PullRequest {{
                            {self._get_pr_commits_query_block(enable_paging=True)}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self._page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.pr.commitsQuery'
        ):
            for api_pr_commit in page['data']['organization']['repo']['pr']['commitsQuery'][
                'commits'
            ]:
                # Commit blocks are nested within the 'commits' block
                yield api_pr_commit['commit']
