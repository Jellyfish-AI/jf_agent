import logging

from jira import JIRA
from jira.resources import GreenHopperResource

from jf_agent import agent_logging, diagnostics, download_and_write_streaming, write_file
from jf_agent.jf_jira.jira_download import (
    download_boards_and_sprints,
    download_customfieldoptions,
    download_fields,
    download_issue_batch,
    download_issuelinktypes,
    download_issuetypes,
    download_priorities,
    download_projects_and_versions,
    download_resolutions,
    download_users,
    download_worklogs,
)

logger = logging.getLogger(__name__)


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def get_basic_jira_connection(config, creds):
    try:
        return JIRA(
            server=config.jira_url,
            basic_auth=(creds.jira_username, creds.jira_password),
            max_retries=3,
            options={
                'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
                'verify': not config.skip_ssl_verification,
            },
        )
    except Exception as e:
        agent_logging.log_and_print(
            logger, logging.ERROR, f'Failed to connect to Jira:\n{e}', exc_info=True
        )


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def print_all_jira_fields(config, jira_connection):
    for f in download_fields(
        jira_connection, config.jira_include_fields, config.jira_exclude_fields
    ):
        print(f"{f['key']:30}\t{f['name']}")


@diagnostics.capture_timing()
@agent_logging.log_entry_exit(logger)
def load_and_dump_jira(config, jira_connection):
    try:
        write_file(
            config.outdir,
            'jira_fields',
            config.compress_output_files,
            download_fields(
                jira_connection, config.jira_include_fields, config.jira_exclude_fields
            ),
        )

        projects_and_versions = download_projects_and_versions(
            jira_connection,
            config.jira_include_projects,
            config.jira_exclude_projects,
            config.jira_include_project_categories,
            config.jira_exclude_project_categories,
        )

        project_ids = {proj['id'] for proj in projects_and_versions}
        write_file(
            config.outdir,
            'jira_projects_and_versions',
            config.compress_output_files,
            projects_and_versions,
        )

        write_file(
            config.outdir,
            'jira_users',
            config.compress_output_files,
            download_users(jira_connection, config.jira_gdpr_active),
        )
        write_file(
            config.outdir,
            'jira_resolutions',
            config.compress_output_files,
            download_resolutions(jira_connection),
        )
        write_file(
            config.outdir,
            'jira_issuetypes',
            config.compress_output_files,
            download_issuetypes(jira_connection, project_ids),
        )
        write_file(
            config.outdir,
            'jira_linktypes',
            config.compress_output_files,
            download_issuelinktypes(jira_connection),
        )
        write_file(
            config.outdir,
            'jira_priorities',
            config.compress_output_files,
            download_priorities(jira_connection),
        )

        def download_and_write_boards_and_sprints():
            boards, sprints, links = download_boards_and_sprints(jira_connection, project_ids)
            write_file(config.outdir, 'jira_boards', config.compress_output_files, boards)
            write_file(config.outdir, 'jira_sprints', config.compress_output_files, sprints)
            write_file(
                config.outdir, 'jira_board_sprint_links', config.compress_output_files, links
            )

        download_and_write_boards_and_sprints()

        @diagnostics.capture_timing()
        @agent_logging.log_entry_exit(logger)
        def download_and_write_issues():
            return download_and_write_streaming(
                config.outdir,
                'jira_issues',
                config.compress_output_files,
                generator_func=download_issue_batch,
                generator_func_args=(
                    jira_connection,
                    project_ids,
                    config.jira_include_fields,
                    config.jira_exclude_fields,
                    config.jira_issue_jql,
                ),
                item_id_dict_key='id',
            )

        issue_ids = download_and_write_issues()

        write_file(
            config.outdir,
            'jira_worklogs',
            config.compress_output_files,
            download_worklogs(jira_connection, issue_ids),
        )
        write_file(
            config.outdir,
            'jira_customfieldoptions',
            config.compress_output_files,
            download_customfieldoptions(jira_connection),
        )

        return {'type': 'Jira', 'status': 'success'}
    except Exception as e:
        agent_logging.log_and_print(
            logger, logging.ERROR, f'Failed to download jira data:\n{e}', exc_info=True
        )

        return {'type': 'Jira', 'status': 'failed'}
