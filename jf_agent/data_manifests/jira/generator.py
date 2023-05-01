import logging
from jf_agent import agent_logging

from jf_agent.data_manifests.jira.adapter import JiraCloudManifestAdapter

from jf_agent.data_manifests.jira.manifest import JiraDataManifest
from jf_agent.data_manifests.manifest import ManifestSource


logger = logging.getLogger(__name__)


class UnsupportedJiraProvider(Exception):
    pass


def create_manifest(company_slug, config, creds):
    manifest_adapter = JiraCloudManifestAdapter(config=config, creds=creds)

    agent_logging.log_and_print(
        logger, logging.INFO, f'Generating Jira Manifest data for {company_slug}...'
    )

    users_count = manifest_adapter.get_users_count()
    fields_count = manifest_adapter.get_fields_count()
    resolutions_count = manifest_adapter.get_resolutions_count()
    issue_types_count = manifest_adapter.get_issue_types_count()
    issue_link_types_count = manifest_adapter.get_issue_link_types_count()
    priorities_count = manifest_adapter.get_priorities_count()
    projects_count = manifest_adapter.get_projects_count()
    project_versions_count = manifest_adapter.get_project_versions_count()
    boards_count = manifest_adapter.get_boards_count()
    sprints_count = manifest_adapter.get_sprints_count()
    issues_count = manifest_adapter.get_issues_count()
    issues_count = manifest_adapter.get_issues_data_count()

    agent_logging.log_and_print(logger, logging.INFO, 'Done generating manifest data!')

    jira_manifest = JiraDataManifest(
        company=company_slug,
        data_source=ManifestSource.remote,
        users_count=users_count,
        fields_count=fields_count,
        resolutions_count=resolutions_count,
        issue_types_count=issue_types_count,
        issue_link_types_count=issue_link_types_count,
        priorities_count=priorities_count,
        projects_count=projects_count,
        project_versions_count=project_versions_count,
        boards_count=boards_count,
        sprints_count=sprints_count,
        issues_count=issues_count,
    )

    return jira_manifest
