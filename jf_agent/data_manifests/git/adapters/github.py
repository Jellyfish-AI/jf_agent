from dateutil import parser as datetime_parser
from typing import Generator

from jf_agent.data_manifests.git.adapters.manifest_adapter import ManifestAdapter
from jf_agent.data_manifests.git.manifest import (
    GitBranchManifest,
    GitPullRequestManifest,
    GitRepoManifest,
    GitUserManifest,
)
from jf_agent.data_manifests.manifest import ManifestSource
from jf_agent.git.github_gql_client import GithubGqlClient


# TODO: Expand or generalize this to work with things other than github (BBCloud, Gitlab, etc)
class GithubManifestGenerator(ManifestAdapter):
    '''
    Basic client for probing a GH instance. 
    '''

    def __init__(
        self, token: str, base_url: str, company: str, org: str, instance: str, verify=True,
    ) -> None:
        # Super class fields
        self.company = company
        self.org = org
        self.instance = instance
        # Session fields
        self.token = token

        self.client = GithubGqlClient(base_url=base_url, token=token)

    def get_users_count(self) -> int:
        return self.client.get_users_count(login=self.org)

    def get_repos_count(self) -> int:
        return self.client.get_repos_count(login=self.org)

    def get_all_repo_data(self) -> Generator[GitRepoManifest, None, None]:
        for repo in self.client.get_repo_manifest_data(login=self.org, page_size=50):
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
                commits_on_default_branch=repo['defaultBranch']['target']['history']['totalCount']
                if repo['defaultBranch']
                else 0,
                default_branch_name=repo['defaultBranch']['name']
                if repo['defaultBranch']
                else None,
            )

    def get_all_user_data(self) -> Generator[GitUserManifest, None, None]:
        for user in self.client.get_users(login=self.org):
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

    def get_all_branch_data(
        self, repo_name: str, repo_id: int
    ) -> Generator[GitBranchManifest, None, None]:
        for branch in self.client.get_branches(login=self.org, repo_name=repo_name):
            yield GitBranchManifest(
                company=self.company,
                data_source=ManifestSource.remote,
                org=self.org,
                instance=self.instance,
                repository_name=repo_name,
                repository_id=repo_id,
                branch_name=branch['name'],
            )

    def get_all_pr_data(self, repo_name: str) -> Generator[GitPullRequestManifest, None, None]:
        for pr in self.client.get_pr_manifest_data(login=self.org, repo_name=repo_name):
            yield GitPullRequestManifest(
                company=self.company,
                data_source=ManifestSource.remote,
                org=self.org,
                instance=self.instance,
                repository_name=pr['repository']['name'],
                repository_id=pr['repository']['id'],
                pull_request_id=pr['id'],
                pull_request_title=pr['title'],
                pull_request_number=int(pr['number']),
                last_update=datetime_parser.parse(pr['updatedAt']),
            )
