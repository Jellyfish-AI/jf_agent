from concurrent.futures import ThreadPoolExecutor
import logging

from jf_agent.data_manifests.jira.adapter import JiraCloudManifestAdapter

from jf_agent.data_manifests.jira.manifest import JiraDataManifest, JiraProjectManifest
from jf_agent.data_manifests.manifest import ManifestSource
from jira import JIRAError


logger = logging.getLogger(__name__)


class ProjectManifestGenerationException(Exception):
    pass


class UnsupportedJiraProvider(Exception):
    pass


def create_manifest(company_slug, config, creds):
    manifest_adapter = JiraCloudManifestAdapter(
        company_slug=company_slug, config=config, creds=creds
    )

    def _agent_log(msg: str):
        logger.info(msg)

    project_data_dicts = manifest_adapter.get_project_data_dicts()

    jira_manifest = None
    # Total threads includes all potential project manifests
    # plus one 'global' jira manifest thread that will get
    # all totals (sprints, boards, issues, etc)
    with ThreadPoolExecutor(max_workers=5) as executor:

        _agent_log(f'Processing {len(project_data_dicts)} Jira Projects...')

        # Create future for global Jira Manifest. This will be our main JiraManifest,
        # and we will add Project Manifests to it
        generate_base_manifest_future = executor.submit(process_global_jira_data, manifest_adapter)

        # generate futures for project manifests
        project_future_to_project_key = {
            executor.submit(
                generate_project_manifest, manifest_adapter, project_data_dict
            ): project_data_dict['key']
            for project_data_dict in project_data_dicts
        }

        # Process future results and handle exceptions
        project_keys_to_errors = {}
        project_manifests: list[JiraProjectManifest] = []
        for future in project_future_to_project_key.keys():
            # Track exceptions and log them in a dictionary
            # so we can map project keys to detected exceptions
            if future.exception():
                project_keys_to_errors[project_future_to_project_key[future]] = future.exception()
            else:
                project_manifests.append(future.result())

        _agent_log(f'Done processing {len(project_data_dicts)} Jira Projects!')

        # Load base Jira Manifest object from globals generator
        jira_manifest = generate_base_manifest_future.result()
        # Append additional manifests (only project manifests, for now)
        jira_manifest.project_manifests = sorted(project_manifests, key=lambda p: p.project_key)
        jira_manifest.encountered_errors_for_projects = project_keys_to_errors

        _agent_log('Done processing Jira Manifest!')

    return jira_manifest


def process_global_jira_data(manifest_adapter: JiraCloudManifestAdapter) -> JiraDataManifest:

    total_users_count = manifest_adapter.get_users_count()
    total_fields_count = manifest_adapter.get_fields_count()
    total_resolutions_count = manifest_adapter.get_resolutions_count()
    total_issue_types_count = manifest_adapter.get_issue_types_count()
    total_issue_link_types_count = manifest_adapter.get_issue_link_types_count()
    total_priorities_count = manifest_adapter.get_priorities_count()
    total_boards_count = manifest_adapter.get_boards_count()
    project_data_dicts = manifest_adapter.get_project_data_dicts()
    project_versions_count = manifest_adapter.get_project_versions_count()
    issues_count = manifest_adapter.get_issues_count()

    return JiraDataManifest(
        company=manifest_adapter.company_slug,
        data_source=ManifestSource.remote,
        users_count=total_users_count,
        fields_count=total_fields_count,
        resolutions_count=total_resolutions_count,
        issue_types_count=total_issue_types_count,
        issue_link_types_count=total_issue_link_types_count,
        priorities_count=total_priorities_count,
        projects_count=len(project_data_dicts),
        boards_count=total_boards_count,
        project_versions_count=project_versions_count,
        issues_count=issues_count,
        # The following fields must be filled out after processing
        # all ProjectManifests
        project_manifests=[],
        encountered_errors_for_projects={},
    )


def generate_project_manifest(
    manifest_adapter: JiraCloudManifestAdapter, project_data_dict: dict
) -> JiraProjectManifest:

    project_key = project_data_dict['key']
    project_id = int(project_data_dict['id'])
    try:

        # FIRST, DO A BASIC TEST TO SEE IF WE HAVE THE PROPER PERMISSIONS
        # TO SEE ANY DATA IN THIS PROJECT
        if not manifest_adapter.test_basic_auth_for_project(project_id=project_id):
            raise ProjectManifestGenerationException(
                f'Exception encountered when trying to do basic auth test against Jira Project {project_key}. It is very likely that we do not have permissions to this Project.'
            )

        issues_count = manifest_adapter.get_issues_count_for_project(project_id=project_id)
        version_count = manifest_adapter.get_project_versions_count_for_project(
            project_id=project_id
        )

        return JiraProjectManifest(
            company=manifest_adapter.company_slug,
            data_source=ManifestSource.remote,
            project_id=project_id,
            project_key=project_key,
            issues_count=issues_count,
            version_count=version_count,
        )
    except JIRAError as e:
        raise ProjectManifestGenerationException(e.text)
    except Exception as e:
        raise ProjectManifestGenerationException(str(e))
