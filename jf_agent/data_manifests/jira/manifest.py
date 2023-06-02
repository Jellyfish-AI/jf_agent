from dataclasses import dataclass
from typing import TypeVar

from jf_agent.data_manifests.manifest import Manifest, ManifestSource


IJiraDataManifest = TypeVar('IJiraDataManifest', bound='JiraDataManifest')


def _get_jira_manifest_full_name(manifest: Manifest):
    return f'{manifest.manifest_type}_{manifest.company}'


@dataclass
class JiraDataManifest(Manifest):
    # Counts
    users_count: int
    fields_count: int
    resolutions_count: int
    issue_types_count: int
    issue_link_types_count: int
    priorities_count: int
    projects_count: int
    project_versions_count: int
    boards_count: int
    sprints_count: int
    issues_count: int
    project_keys: list[str]

    def get_manifest_full_name(self):
        return _get_jira_manifest_full_name(self)

    def __eq__(self, __o: Manifest) -> bool:
        return super().__eq__(__o)
