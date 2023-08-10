from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator

from jf_agent.data_manifests.git.manifest import (
    GitBranchManifest,
    GitPullRequestManifest,
    GitRepoManifest,
    GitUserManifest,
)

# TODO: Expand or generalize this to work with things other than github (BBCloud, Gitlab, etc)
@dataclass
class ManifestAdapter(ABC):
    '''
    Abstract class for getting different git manifests
    '''

    company: str
    instance: str
    org: str

    @abstractmethod
    def get_users_count(self) -> int:
        pass

    @abstractmethod
    def get_repos_count(self) -> int:
        pass

    @abstractmethod
    def get_all_repo_data(self) -> Generator[GitRepoManifest, None, None]:
        pass

    @abstractmethod
    def get_all_user_data(self) -> Generator[GitUserManifest, None, None]:
        pass

    @abstractmethod
    def get_all_branch_data(self, repo_name: str) -> Generator[GitBranchManifest, None, None]:
        pass

    @abstractmethod
    def get_all_pr_data(self, repo_name: str) -> Generator[GitPullRequestManifest, None, None]:
        pass
