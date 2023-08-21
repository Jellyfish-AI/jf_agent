from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from jf_agent.data_manifests.manifest import Manifest, ManifestSource


IGitDataManifest = TypeVar('IGitDataManifest', bound='GitDataManifest')
IGitRepoManifest = TypeVar('IGitRepoManifest', bound='GitRepoManifest')
IGitUserManifest = TypeVar('IGitUserManifest', bound='GitUserManifest')
IGitPullRequestManifest = TypeVar('IGitPullRequestManifest', bound='GitPullRequestManifest')

# This is the parent class for all GitManifest type classes. It inherits
# from manifests, but ensures that all 'GitManifests' have an instance
@dataclass
class GitManifest(Manifest):
    instance: str
    org: str

    def __eq__(self, __o: IGitRepoManifest) -> bool:
        return super().__eq__(__o)

    def __hash__(self):
        return super().__hash__()

    # Function used for doing class comparisons
    def get_manifest_full_name(self):
        return f'{self.manifest_type}_{self.company}_{self.instance}_{self.org}'


@dataclass
class GitDataManifest(GitManifest):
    users_count: int
    repos_count: int
    repo_manifests: list[IGitRepoManifest]
    user_manifests: list[IGitUserManifest]

    def _find_unique_manifests(
        self, minuend_list: list[Manifest], subtrahend_list: list[Manifest]
    ) -> list[Any]:
        return list(set(minuend_list) - (set(subtrahend_list)))

    def __eq__(self, __o: IGitRepoManifest) -> bool:
        return super().__eq__(__o)

    def __hash__(self):
        return super().__hash__()

    # Function used for doing class comparisons
    def get_manifest_full_name(self):
        return super().get_manifest_full_name()


@dataclass
class GitUserManifest(GitManifest):

    user_id: str
    name: str
    login: str
    url: str
    email: str

    # Function used for doing class comparisons
    def get_manifest_full_name(self):
        return f'{super().get_manifest_full_name()}_{self.user_id}'

    def __hash__(self):
        return super().__hash__()

    def __eq__(self, __o: IGitRepoManifest) -> bool:
        return super().__eq__(__o)


@dataclass
class GitPullRequestManifest(GitManifest):

    repository_id: str
    repository_name: str
    # This is the unique ID that represents
    # a PR object, like a GUID
    # In github, it is an int. Unsure what it is
    # in BBCloud or GitLab
    pull_request_id: str
    # This is the pull request number, relevant
    # to it's encapsulating repository
    pull_request_number: int
    pull_request_title: str
    last_update: datetime

    # Function used for doing class comparisons
    def get_manifest_full_name(self):
        return f'{super().get_manifest_full_name()}_{self.repository_id}_{self.pull_request_id}'


@dataclass
class GitBranchManifest(GitManifest):

    repository_id: str
    repository_name: str
    branch_name: str

    # Function used for doing class comparisons
    def get_manifest_full_name(self):
        return f'{super().get_manifest_full_name()}_{self.repository_id}_{self.branch_name}'


@dataclass
class GitRepoManifest(GitManifest):

    repository_id: str
    repository_name: str
    repository_full_name: str
    url: str
    # Count Data
    user_count: int
    pull_request_count: int
    branch_count: int
    default_branch_name: str
    commits_on_default_branch: int

    # TODO: These will always be empty until we implement the proper function implemented
    # in the SQL Manifest adapter. These will also potentially always be empty if 'Verbose'
    # mode is not run in the git manifest generator
    pull_request_manifests: list[GitPullRequestManifest] = field(default_factory=list)
    branch_manifests: list[GitBranchManifest] = field(default_factory=list)

    # Function used for doing class comparisons
    def get_manifest_full_name(self):
        return f'{super().get_manifest_full_name()}_{self.repository_id}'

    def __hash__(self):
        return super().__hash__()

    def __eq__(self, __o: IGitRepoManifest) -> bool:
        return super().__eq__(__o)
