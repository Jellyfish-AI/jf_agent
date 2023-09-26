import json
import logging
from datetime import datetime
import time
from requests import Session
import requests
from requests.utils import default_user_agent
from typing import Generator
from jf_agent.git.github_gql_utils import datetime_to_gql_str_format, github_gql_format_to_datetime

from jf_agent.session import retry_session

logger = logging.getLogger(__name__)


class GqlRateLimitedException(Exception):
    pass


class GithubGqlClient:

    GITHUB_GQL_USER_FRAGMENT = "... on User {login, id: databaseId, email, name, url}"
    GITHUB_GQL_PAGE_INFO_BLOCK = "pageInfo {hasNextPage, endCursor}"
    # Need to make a second, special actor fragment to make sure we grab
    # the proper ID from either a bot or a User
    GITHUB_GQL_ACTOR_FRAGMENT = """
        ... on Actor 
            { 
                login 
                ... on User { id: databaseId, email, name } 
                ... on Bot { id: databaseId}
            }
    """
    # NOTE: On the author block here, we have a type GitActor
    # We cannot always get the email from the nested user object,
    # so pull whatever email we can from the gitActor top level object.
    # (we can't get the email from the user object bc of variable privacy configuration)
    GITHUB_GQL_COMMIT_FRAGMENT = f"""
        ... on Commit {{
            sha: oid
            url
            author {{
                ... on GitActor {{
                    email
                    name
                    user {{ id: databaseId, login }}
                }}
            }}
            message
            committedDate
            authoredDate
            parents {{totalCount}}
        }}
    """
    GITHUB_GQL_SHORT_REPO_FRAGMENT = "... on Repository {name, id:databaseId, url}"

    # The PR query is HUGE, we shouldn't query more than about 25 at a time
    MAX_PAGE_SIZE_FOR_PR_QUERY = 25

    def __init__(
        self, base_url: str, token: str, verify: bool = True, session: Session = None
    ) -> None:
        self.token = token
        self.base_url = self.get_github_gql_base_url(base_url=base_url)
        # We need to hit the REST API for one API call, see get_organization_by_login
        self.rest_api_url = base_url or 'https://api.github.com'

        if not session:
            session = retry_session()

        self.session = self.get_github_gql_session(token=token, verify=verify, session=session)

    def get_github_gql_base_url(self, base_url: str):
        if base_url and 'api/v3' in base_url:
            # Github server clients provide an API with a trailing '/api/v3'
            # replace this with the graphql endpoint
            return base_url.replace('api/v3', 'api/graphql')
        else:
            return 'https://api.github.com/graphql'

    def get_github_gql_session(self, token: str, verify: bool = True, session: Session = None):
        if not session:
            session = retry_session()

        session.verify = verify
        session.headers.update(
            {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github+json',
                'User-Agent': f'jellyfish/1.0 ({default_user_agent()})',
            }
        )
        return session

    def page_results(
        self, query_body: str, path_to_page_info: str, cursor: str = 'null'
    ) -> Generator[dict, None, None]:

        # TODO: Write generalized paginator
        hasNextPage = True
        while hasNextPage:
            # Fetch results
            result = self.get_raw_result(query_body=(query_body % cursor))

            yield result

            # Get relevant data and yield it
            path_tokens = path_to_page_info.split('.')
            for token in path_tokens:
                result = result[token]

            page_info = result['pageInfo']
            # Need to grab the cursor and wrap it in quotes
            _cursor = page_info['endCursor']
            # If endCursor returns null (None), break out of loop
            hasNextPage = page_info['hasNextPage'] and _cursor
            cursor = f'"{_cursor}"'

    # This includes retry logic!
    def get_raw_result(self, query_body: str) -> dict:
        max_attempts = 7
        attempt_number = 1
        while True:
            try:
                response = self.session.post(url=self.base_url, json={'query': query_body})

                response.raise_for_status()
                json_str = response.content.decode()
                json_data = json.loads(json_str)
                if 'errors' in json_data:
                    if len(json_data['errors']) == 1:
                        error = json_data['errors'][0]
                        if error.get('type') == 'RATE_LIMITED':
                            raise GqlRateLimitedException(
                                error.get('message', 'Rate Limit hit in GQL')
                            )
                    raise Exception(
                        f'Exception encountered when trying to query: {query_body}. Error: {json_data["errors"]}'
                    )
                return json_data
            # We can get transient 403 level errors that have to do with rate limiting,
            # but aren't directly related to the above GqlRateLimitedException logic.
            # Do a simple retry loop here
            except requests.exceptions.HTTPError as e:
                if e.response.status_code != 403:
                    raise
                if attempt_number > max_attempts:
                    raise

                sleep_time = attempt_number ** 2
                # Overwrite sleep time if github gives us a specific wait time
                if e.response.headers.get('retry-after') and attempt_number == 1:
                    retry_after = int(e.response.headers.get('retry-after'))
                    if retry_after > (60 * 5):
                        # if the given wait time is more than 5 minutes, call their bluff
                        # and try the experimental backoff approach
                        pass
                    elif retry_after <= 0:
                        # if the given wait time is negative ignore their suggestion
                        pass
                    else:
                        # Add three seconds for gracetime
                        sleep_time = retry_after + 3

                logger.warning(
                    f'A secondary rate limit was hit. Sleeping for {sleep_time} seconds. (attempt {attempt_number}/{max_attempts})',
                )
                time.sleep(sleep_time)
            except GqlRateLimitedException:
                if attempt_number > max_attempts:
                    raise

                rate_limit_info = self.get_rate_limit(base_url=self.base_url)
                reset_at: datetime = github_gql_format_to_datetime(rate_limit_info['resetAt'])
                reset_at_timestamp = reset_at.timestamp()
                curr_timestamp = datetime.utcnow().timestamp()

                sleep_time = reset_at_timestamp - curr_timestamp

                # Sometimes github gives a reset time way in the
                # future. But rate limits reset each hour, so don't
                # wait longer than that
                sleep_time = min(sleep_time, 3600)

                # Sometimes github gives a reset time in the
                # past. In that case, wait for 5 mins just in case.
                if sleep_time <= 0:
                    sleep_time = 300
                logger.warning(f'GQL Rate Limit hit. Sleeping for {sleep_time} seconds',)
                time.sleep(sleep_time)
            finally:
                attempt_number += 1

    # Getting the rate limit info is never affected by the current rate limit
    def get_rate_limit(self, base_url: str):
        response = self.session.post(
            url=base_url, json={'query': '{rateLimit {remaining, resetAt}}'}
        )
        response.raise_for_status()
        json_str = response.content.decode()
        return json.loads(json_str)['data']['rateLimit']

    # This is for commits, specifically the 'author' block within them.
    # On the GQL side of things, these are specifically a distinct type of object,
    # GitActor. It has a nested user object, but the quality of data within it
    # is variable due to a users privacy settings. Email, for example, is often
    # not present in the child user block, so we always grab it from the top level.
    @staticmethod
    def _process_git_actor_gql_object(author: dict) -> dict:
        user: dict = author.get('user') or {}
        return {
            'id': user.get('id'),
            'login': user.get('login'),
            'email': author['email'],
            'name': author['name'],
        }

    # HACK: This call will actually use the REST endpoint
    # Agent clients are supposed to have the [org:read] scope,
    # but many of them don't. This wasn't a problem before
    # because the REST org API doesn't actually hide behind any perms...
    # TODO: Once we straighten out everybody's permissions we can sunset
    # this function
    def get_organization_by_login(self, login: str):
        # NOTE: We are hitting a different base url here!
        url = f'{self.rest_api_url}/orgs/{login}'

        result = self.session.get(url)

        # HACK: This appears to happen after we have been
        # rate-limited when hitting certain URLs, there is
        # likely a more elegant way to solve this but it takes
        # about an hour to test each time and it works.
        # NOTE: This is unlikely for this call, because we are only hitting
        # the org once for an entire git config
        if result.status_code == 403:
            result = self.session.get(url)

        result.raise_for_status()
        return result.json()

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
        for page in self.page_results(
            query_body=query_body, path_to_page_info='data.organization.userQuery'
        ):
            for user in page['data']['organization']['userQuery']['users']:
                yield user

    def get_repos(
        self, login: str, repo_filters: list[filter] = None
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
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
                                    target {{
                                        ... on Commit {{
                                            mostRecentCommits: history(first: 1, after: null) {{
                                                commits: nodes {{ committedDate }}
                                            }}
                                        }}
                                    }}
                            }}
                            prQuery: pullRequests(first: 1, after: null, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
                                prs: nodes {{ updatedAt }}
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
        for page in self.page_results(
            query_body=query_body, path_to_page_info='data.organization.repoQuery'
        ):
            for api_repo in page['data']['organization']['repoQuery']['repos']:
                # Skip over excluded or ignore non-included
                if not all(filt(api_repo) for filt in repo_filters):
                    continue
                else:
                    # Potentially process more branches
                    if api_repo['branchQuery']['pageInfo']['hasNextPage']:
                        api_repo['branches'] = [
                            branch
                            for branch in self.get_branches(login=login, repo_name=api_repo['name'])
                        ]
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
        for page in self.page_results(
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
                                    history(first: 100, since: "{datetime_to_gql_str_format(since)}", after: %s) {{
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
        for page in self.page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.branch.target.history'
        ):
            for api_commit in page['data']['organization']['repo']['branch']['target']['history'][
                'commits'
            ]:
                # Overwrite Author block for backwards compatibility
                api_commit['author'] = self._process_git_actor_gql_object(api_commit['author'])
                yield api_commit

    #
    # PR Queries are HUGE, so pull out reusable blocks (comments, reviews, commits, etc)
    #
    def _get_pr_comments_query_block(self, enable_paging: bool = False):
        return f"""
            commentsQuery: comments(first: 100{', after: %s' if enable_paging else ''}) {{
                {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                
                comments: nodes {{
                    author {{
                        {self.GITHUB_GQL_ACTOR_FRAGMENT}
                    }}
                    body
                    createdAt
                }}
            }}
        """

    # NOTE: There are comments associated with reviews that we need to fetch as well
    def _get_pr_reviews_query_block(self, enable_paging: bool = False):
        return f"""
            reviewsQuery: reviews(first: 25{', after: %s' if enable_paging else ''}) {{
                {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                
                reviews: nodes {{
                    ... on PullRequestReview {{
                        author {{
                            {self.GITHUB_GQL_ACTOR_FRAGMENT}
                        }}
                        id: databaseId
                        state
                        # NOTE! We are paging for comments here as well!
                        {self._get_pr_comments_query_block()}
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

    # PR query is HUGE, see above GITHUB_GQL_PR_* blocks for reused code
    # page_size is optimally variable. Most repos only have a 0 to a few PRs day to day,
    # so sometimes the optimal page_size is 0. Generally, we should never go over 25
    def get_prs(
        self,
        login: str,
        repo_name: str,
        include_top_level_comments: bool = False,
        page_size: int = MAX_PAGE_SIZE_FOR_PR_QUERY,
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                repo: repository(name: "{repo_name}") {{
                    prQuery: pullRequests(first: {page_size}, orderBy: {{direction: DESC, field: UPDATED_AT}}, after: %s) {{
                        {self.GITHUB_GQL_PAGE_INFO_BLOCK}
                        prs: nodes {{
                            ... on PullRequest {{
                                id: number
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
                                baseRefName
                                headRefName
                                baseRepository {{ {self.GITHUB_GQL_SHORT_REPO_FRAGMENT} }}
                                headRepository {{ {self.GITHUB_GQL_SHORT_REPO_FRAGMENT} }}
                                author {{
                                    {self.GITHUB_GQL_ACTOR_FRAGMENT}
                                }}
                                mergedBy {{
                                    {self.GITHUB_GQL_ACTOR_FRAGMENT}
                                }}
                                mergeCommit {{
                                    {self.GITHUB_GQL_COMMIT_FRAGMENT}
                                }}
                                {self._get_pr_comments_query_block(enable_paging=False) if include_top_level_comments else ''}
                                {self._get_pr_reviews_query_block(enable_paging=False)}
                                {self._get_pr_commits_query_block(enable_paging=False)}
                            }}
                        }}
                    }}
                }}
            }}
        }}
        """
        for page in self.page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.prQuery'
        ):
            for api_pr in page['data']['organization']['repo']['prQuery']['prs']:
                # Process and add related PR data (comments, reviews, commits)
                # This may require additional API calls
                pr_number = api_pr['number']

                # Load reviews first because we use them in both reviews and comments
                reviews = (
                    [r for r in self.get_pr_reviews(login, repo_name, pr_number=pr_number)]
                    if api_pr['reviewsQuery']['pageInfo']['hasNextPage']
                    else api_pr['reviewsQuery']['reviews']
                )

                # NOTE: COMMENTS ARE WEIRD! They exist in there own API endpoint (these
                # are typically top level comments in a PR, considered an IssueComment)
                # but there are also comments associated with each review (typically only one)
                # The baseline for what we care about is the Review Level comment, pulled from
                # the reviews endpoint. Grabbing Top Level Comments is an optional feature flag

                # Grab the comments pulled from reviews. We ALWAYS want these!
                api_pr['comments'] = [
                    comment for review in reviews for comment in review['commentsQuery']['comments']
                ]

                # Grab the potentially optional top level comments
                if include_top_level_comments:
                    top_level_comments = (
                        [
                            comment
                            for comment in self.get_pr_top_level_comments(
                                login, repo_name, pr_number=pr_number
                            )
                        ]
                        if api_pr['commentsQuery']['pageInfo']['hasNextPage']
                        else api_pr['commentsQuery']['comments']
                    )
                    api_pr['comments'].extend(top_level_comments)

                api_pr['reviews'] = reviews

                api_pr['commits'] = (
                    [
                        commit
                        for commit in self.get_pr_commits(login, repo_name, pr_number=pr_number)
                    ]
                    if api_pr['commitsQuery']['pageInfo']['hasNextPage']
                    else [commit['commit'] for commit in api_pr['commitsQuery']['commits']]
                )

                # Do some extra processing on commits to clean up their weird author block
                for commit in api_pr['commits']:
                    commit['author'] = self._process_git_actor_gql_object(commit['author'])

                if api_pr['mergeCommit'] and api_pr['mergeCommit']['author']:
                    api_pr['mergeCommit']['author'] = self._process_git_actor_gql_object(
                        api_pr['mergeCommit']['author']
                    )

                yield api_pr

    def get_pr_top_level_comments(
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
        for page in self.page_results(
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
        for page in self.page_results(
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
        for page in self.page_results(
            query_body=query_body, path_to_page_info='data.organization.repo.pr.commitsQuery'
        ):
            for api_pr_commit in page['data']['organization']['repo']['pr']['commitsQuery'][
                'commits'
            ]:
                # Commit blocks are nested within the 'commits' block
                commit = api_pr_commit['commit']
                commit['author'] = self._process_git_actor_gql_object(commit['author'])
                yield commit

    def get_users_count(self, login: str) -> int:
        query_body = f"""{{
            organization(login: "{login}"){{
                    users: membersWithRole {{
                        totalCount
                    }}
                }}
            }}
        """
        # TODO: Maybe serialize the return results so that we don't have to do this crazy nested grabbing?
        return self.get_raw_result(query_body=query_body)['data']['organization']['users'][
            'totalCount'
        ]

    def get_repos_count(self, login: str) -> int:
        query_body = f"""{{
            organization(login: "{login}"){{
                    repos: repositories {{
                        totalCount
                    }}
                }}
            }}
        """
        # TODO: Maybe serialize the return results so that we don't have to do this crazy nested grabbing?
        return self.get_raw_result(query_body=query_body)['data']['organization']['repos'][
            'totalCount'
        ]

    def get_repo_manifest_data(
        self, login: str, page_size: int = 10
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
            organization(login: "{login}") {{
                    repositories(first: {page_size}, after: %s) {{
                        pageInfo {{
                            endCursor
                            hasNextPage
                            
                        }}
                        repos: nodes {{
                            id: databaseId
                            name
                            url
                            defaultBranch: defaultBranchRef {{
                                name
                                target {{
                                    ... on Commit {{
                                        history {{
                                            totalCount
                                        }}
                                    }}
                                }}
                            }}
                            users: assignableUsers{{
                                totalCount
                            }}
                            prs: pullRequests {{
                                totalCount
                            }}
                            branches: refs(refPrefix:"refs/heads/") {{
                                totalCount
                            }}
                        }}
                    }}
                }}
            }}
        """
        path_to_page_info = 'data.organization.repositories'
        for result in self.page_results(query_body=query_body, path_to_page_info=path_to_page_info):
            for repo in result['data']['organization']['repositories']['repos']:
                yield repo

    def get_pr_manifest_data(
        self, login: str, repo_name: str, page_size=100
    ) -> Generator[dict, None, None]:
        query_body = f"""{{
                organization(login: "{login}") {{
                        repository(name: "{repo_name}") {{
                            name
                            id: databaseId
                            prs_query: pullRequests(first: {page_size}, after: %s) {{
                                pageInfo {{
                                    endCursor
                                    hasNextPage
                                }}
                                totalCount
                                prs: nodes {{
                                    updatedAt
                                    id: databaseId
                                    title
                                    number
                                    repository {{id: databaseId, name}}
                                }}
                            }}
                        }}
                    }}
                }}
        """

        path_to_page_info = 'data.organization.repository.prs_query'
        for result in self.page_results(query_body=query_body, path_to_page_info=path_to_page_info):
            for pr in result['data']['organization']['repository']['prs_query']['prs']:
                yield pr
