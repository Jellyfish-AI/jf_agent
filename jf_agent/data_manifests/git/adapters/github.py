from dateutil import parser as datetime_parser
import json
from typing import Generator

from jf_agent.data_manifests.git.adapters.manifest_adapter import ManifestAdapter
from jf_agent.data_manifests.git.manifest import (
    GitBranchManifest,
    GitPullRequestManifest,
    GitRepoManifest,
    GitTeamManifest,
    GitUserManifest,
)
from jf_agent.data_manifests.manifest import ManifestSource
from jf_agent.session import retry_session


# TODO: Expand or generalize this to work with things other than github (BBCloud, Gitlab, etc)
class GithubManifestGenerator(ManifestAdapter):
    '''
    Basic client for probing a GH instance. 
    '''

    def __init__(
        self, token: str, company: str, org: str, instance: str, verify=True, **kwargs
    ) -> None:
        # Super class fields
        self.company = company
        self.org = org
        self.instance = instance
        # Session fields
        self.token = token
        self.base_url = 'https://api.github.com/graphql'

        self.session = retry_session(**kwargs)
        self.session.verify = verify
        self.session.headers.update(
            {'Authorization': f'token {token}', "Accept": "application/vnd.github+json",}
        )

    def get_users_count(self) -> int:
        query_body = f"""{{
            organization(login: "{self.org}"){{
                    users: membersWithRole {{
                        totalCount
                    }}
                }}
            }}
        """
        # TODO: Maybe serialize the return results so that we don't have to do this crazy nested grabbing?
        return self._get_raw_result(query_body=query_body)['data']['organization']['users'][
            'totalCount'
        ]

    def get_teams_count(self) -> int:
        query_body = f"""{{
            organization(login: "{self.org}"){{
                    teams {{
                        totalCount
                    }}
                }}
            }}
        """
        # TODO: Maybe serialize the return results so that we don't have to do this crazy nested grabbing?
        return self._get_raw_result(query_body=query_body)['data']['organization']['teams'][
            'totalCount'
        ]

    def get_repos_count(self) -> int:
        query_body = f"""{{
            organization(login: "{self.org}"){{
                    repos: repositories {{
                        totalCount
                    }}
                }}
            }}
        """
        # TODO: Maybe serialize the return results so that we don't have to do this crazy nested grabbing?
        return self._get_raw_result(query_body=query_body)['data']['organization']['repos'][
            'totalCount'
        ]

    def get_all_repo_data(self, page_size: int = 10) -> Generator[GitRepoManifest, None, None]:
        query_body = f"""{{
            organization(login: "{self.org}") {{
                    name
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
        for result in self._page_results(
            query_body=query_body, path_to_page_info=path_to_page_info
        ):
            for repo in result['data']['organization']['repositories']['repos']:
                yield GitRepoManifest(
                    company=self.company,
                    instance=self.instance,
                    org=self.org,
                    data_source=ManifestSource.remote,
                    repository_id=str(repo['id']),
                    repository_name=repo['name'],
                    repository_full_name=f'{self.org}/{repo["name"]}',
                    url=repo['url'],
                    user_count=repo['users']['totalCount'],
                    pull_request_count=repo['prs']['totalCount'],
                    branch_count=repo['branches']['totalCount'],
                    commits_on_default_branch=repo['defaultBranch']['target']['history'][
                        'totalCount'
                    ]
                    if repo['defaultBranch']
                    else 0,
                    default_branch_name=repo['defaultBranch']['name']
                    if repo['defaultBranch']
                    else None,
                )

    def get_all_user_data(self, page_size: int = 10) -> Generator[GitUserManifest, None, None]:
        query_body = f"""{{
            organization(login: "{self.org}") {{
                    name
                    users: membersWithRole(first: {page_size}, after: %s) {{
                        pageInfo {{
                            endCursor
                            hasNextPage
                        }}
                        user_details: nodes {{
                            id: databaseId
                            name
                            login
                            url
                            email
                        }}
                    }}
                }}
            }}
        """

        path_to_page_info = 'data.organization.users'
        for result in self._page_results(
            query_body=query_body, path_to_page_info=path_to_page_info
        ):
            for user in result['data']['organization']['users']['user_details']:
                yield GitUserManifest(
                    company=self.company,
                    data_source=ManifestSource.remote,
                    org=self.org,
                    instance=self.instance,
                    user_id=user['id'],
                    name=user['name'],
                    login=user['login'],
                    url=user['url'],
                    email=user['email'],
                )

    def get_all_team_data(self, page_size=100) -> Generator[GitTeamManifest, None, None]:
        query_body = f"""{{
                organization(login: "{self.org}") {{
                    name
                    teams(first: {page_size}, after: %s) {{
                        pageInfo {{
                            endCursor
                            hasNextPage
                        }}
                        team_details: nodes {{
                            id: databaseId
                            name
                            slug
                            members {{
                                totalCount
                            }}
                        }}
                    }}
                }}
            }}
        """

        path_to_page_info = 'data.organization.teams'
        for result in self._page_results(
            query_body=query_body, path_to_page_info=path_to_page_info
        ):
            for team in result['data']['organization']['teams']['team_details']:
                yield GitTeamManifest(
                    company=self.company,
                    data_source=ManifestSource.remote,
                    org=self.org,
                    instance=self.instance,
                    team_id=team['id'],
                    slug=team['slug'],
                    name=team['name'],
                    member_count=team['members']['totalCount'],
                )

    def get_all_branch_data(
        self, repo_name: str, page_size=100
    ) -> Generator[GitBranchManifest, None, None]:
        query_body = f"""{{
                organization(login: "{self.org}") {{
                        name
                        repository(name: "{repo_name}") {{
                            name
                            id: databaseId
                            branches_query: refs(refPrefix:"refs/heads/", first: {page_size}, after: %s) {{
                                pageInfo {{
                                    hasNextPage
                                    endCursor
                                }}
                                branches: nodes {{
                                    name
                                }}
                            }}
                        }}
                    }}
                }}
        """

        path_to_page_info = 'data.organization.repository.branches_query'
        for result in self._page_results(
            query_body=query_body, path_to_page_info=path_to_page_info
        ):
            for branch in result['data']['organization']['repository']['branches_query'][
                'branches'
            ]:
                yield GitBranchManifest(
                    company=self.company,
                    data_source=ManifestSource.remote,
                    org=self.org,
                    instance=self.instance,
                    repository_name=result['data']['organization']['repository']['name'],
                    repository_id=result['data']['organization']['repository']['id'],
                    branch_name=branch['name'],
                )

    def get_all_pr_data(
        self, repo_name: str, page_size=100
    ) -> Generator[GitPullRequestManifest, None, None]:
        query_body = f"""{{
                organization(login: "{self.org}") {{
                        name
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
                                }}
                            }}
                        }}
                    }}
                }}
        """

        path_to_page_info = 'data.organization.repository.prs_query'
        for result in self._page_results(
            query_body=query_body, path_to_page_info=path_to_page_info
        ):
            for pr in result['data']['organization']['repository']['prs_query']['prs']:
                yield GitPullRequestManifest(
                    company=self.company,
                    data_source=ManifestSource.remote,
                    org=self.org,
                    instance=self.instance,
                    repository_name=result['data']['organization']['repository']['name'],
                    repository_id=result['data']['organization']['repository']['id'],
                    pull_request_id=pr['id'],
                    pull_request_title=pr['title'],
                    pull_request_number=int(pr['number']),
                    last_update=datetime_parser.parse(pr['updatedAt']),
                )

    def _page_results(
        self, query_body: str, path_to_page_info: str, cursor: str = 'null'
    ) -> Generator[dict, None, None]:

        # TODO: Write generalized paginator
        hasNextPage = True
        while hasNextPage:
            # Fetch results
            result = self._get_raw_result(query_body=(query_body % cursor))

            yield result

            # Get relevant data and yield it
            path_tokens = path_to_page_info.split('.')
            for token in path_tokens:
                result = result[token]

            page_info = result['pageInfo']
            # Need to grab the cursor and wrap it in quotes
            _cursor = page_info['endCursor']
            cursor = f'"{_cursor}"'
            hasNextPage = page_info['hasNextPage']

    def _get_raw_result(self, query_body: str) -> dict:
        response = self.session.post(url=self.base_url, json={'query': query_body})
        response.raise_for_status()
        json_str = response.content.decode()
        json_data = json.loads(json_str)
        if 'errors' in json_data:
            raise Exception(
                f'Exception encountered when trying to query: {query_body}. Error: {json_data["errors"]}'
            )
        return json_data
