from dataclasses import dataclass
from typing import Any, TypeVar

from jf_agent.data_manifests.manifest import Manifest

IJiraDataManifest = TypeVar('IJiraDataManifest', bound='JiraDataManifest')
IJiraProjectManifest = TypeVar('IJiraProjectManifest', bound='JiraProjectManifest')


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
    issues_count: int

    # Drill down into each project with ProjectManifests
    project_manifests: list[IJiraProjectManifest]

    # For debug purposes. We may want to optionally exclude this when serializing
    encountered_errors_for_projects: Any

    def get_manifest_full_name(self):
        return _get_jira_manifest_full_name(self)


@dataclass
class JiraProjectManifest(Manifest):
    project_id: str
    project_key: str
    issues_count: int
    version_count: int

    def get_manifest_full_name(self):
        return f'{_get_jira_manifest_full_name(self)}_{self.project_key}'

    def __hash__(self):
        return hash(self.get_manifest_full_name())
