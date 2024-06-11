from datetime import datetime
from itertools import chain
import logging
import traceback

from jira import JIRA
from jira.resources import GreenHopperResource

from jf_agent import download_and_write_streaming, write_file
from jf_agent.jf_jira.jira_download import (
    download_boards_and_sprints,
    download_customfieldoptions,
    download_fields,
    download_all_issue_metadata,
    detect_issues_needing_sync,
    detect_issues_needing_re_download,
    download_necessary_issues,
    download_issuelinktypes,
    download_issuetypes,
    download_priorities,
    download_projects_and_versions,
    download_resolutions,
    download_users,
    download_worklogs,
    download_statuses,
    download_missing_repos_found_by_jira,
    download_teams,
    IssueMetadata,
)
from jf_ingest import diagnostics, logging_helper

logger = logging.getLogger(__name__)


def _get_raw_jira_connection(config, creds, max_retries=3):
    kwargs = {
        'server': config.jira_url,
        'max_retries': max_retries,
        'options': {
            'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH,
            'verify': not config.skip_ssl_verification,
            "headers": {
                "Accept": "application/json;q=1.0, */*;q=0.9",
                'Content-Type': 'application/json',
            },
        },
    }
    if creds.jira_username and creds.jira_password:
        kwargs['basic_auth'] = (creds.jira_username, creds.jira_password)
    elif creds.jira_bearer_token:
        # HACK(asm,2021-10-18): This is copypasta from
        # https://github.com/pycontribs/jira/blob/df8a6a9879b48083ba940ef9b00d6543bcea5015/jira/client.py#L307-L315
        # I would like to get bearer token support merged upstream,
        # however this is a short term fix to enable customers who
        # have already disabled basic authentication.
        kwargs['options']['headers'] = {
            'Authorization': f'Bearer {creds.jira_bearer_token}',
            'Cache-Control': 'no-cache',
            'Content-Type': 'application/json',
            'Accept': "application/json;q=1.0, */*;q=0.9",
            'X-Atlassian-Token': 'no-check',
        }
    else:
        raise RuntimeError(
            'No valid Jira credentials found! Check your JIRA_USERNAME, JIRA_PASSWORD, or JIRA_BEARER_TOKEN environment variables.'
        )

    jira_conn = JIRA(**kwargs)

    jira_conn._session.headers[
        'User-Agent'
    ] = f'jellyfish/1.0 ({jira_conn._session.headers["User-Agent"]})'

    return jira_conn


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def get_basic_jira_connection(config, creds):
    try:
        return _get_raw_jira_connection(config, creds)
    except Exception as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[e], error_code=2102, exc_info=True
        )


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def print_all_jira_fields(config, jira_connection):
    for f in download_fields(
        jira_connection, config.jira_include_fields, config.jira_exclude_fields
    ):
        # This could potential data that clients do not exposed. Print instead of logging here
        print(f"{f['key']:30}\t{f['name']}")


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def print_missing_repos_found_by_jira(config, creds, issues_to_scan):
    missing_repos = download_missing_repos_found_by_jira(config, creds, issues_to_scan)
    # This could potential data that clients do not exposed. Print instead of logging here
    print(
        f'\nScanning the "Development" field on the Jira issues revealed {len(missing_repos)} Git repos apparently missing from Jellyfish'
    )
    for missing_repo in missing_repos:
        print(f"* {missing_repo['name']:30}\t{missing_repo['url']}")
    print('\n')


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def load_and_dump_jira(config, endpoint_jira_info, jira_connection):
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
            download_users(
                jira_connection,
                config.jira_gdpr_active,
                required_email_domains=config.jira_required_email_domains,
                is_email_required=config.jira_is_email_required,
            ),
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
            boards, sprints, links = download_boards_and_sprints(
                jira_connection, project_ids, config.jira_download_sprints
            )
            write_file(config.outdir, 'jira_boards', config.compress_output_files, boards)
            write_file(config.outdir, 'jira_sprints', config.compress_output_files, sprints)
            write_file(
                config.outdir, 'jira_board_sprint_links', config.compress_output_files, links
            )

        download_and_write_boards_and_sprints()

        issue_metadata_from_jira = download_all_issue_metadata(
            jira_connection,
            project_ids,
            config.jira_earliest_issue_dt,
            config.jira_issue_download_concurrent_threads,
            config.jira_issue_jql,
        )

        issue_metadata_from_jellyfish = {
            int(issue_id): IssueMetadata(
                issue_info['key'],
                datetime.fromisoformat(issue_info['updated']),  # already includes TZ info
            )
            for issue_id, issue_info in endpoint_jira_info['issue_metadata'].items()
        }

        issue_metadata_addl_from_jellyfish = {
            int(issue_id): (
                issue_info.get('epic_link_field_issue_key'),
                issue_info.get('parent_field_issue_key'),
            )
            for issue_id, issue_info in endpoint_jira_info['issue_metadata'].items()
        }

        (
            missing_issue_ids,
            already_up_to_date_issue_ids,
            out_of_date_issue_ids,
            deleted_issue_ids,
        ) = detect_issues_needing_sync(issue_metadata_from_jira, issue_metadata_from_jellyfish)

        logging_helper.send_to_agent_log_file(
            f'Up to date: {len(already_up_to_date_issue_ids)}  out of date: {len(out_of_date_issue_ids)}  '
            f'missing: {len(missing_issue_ids)}  deleted: {len(deleted_issue_ids)}'
        )

        issue_ids_to_download = list(missing_issue_ids.union(out_of_date_issue_ids))

        for fname, vals in [
            ('dbg_jira_issue_metadata_remote', issue_metadata_from_jira),
            ('dbg_jira_issue_metadata_jellyfish', issue_metadata_from_jellyfish),
            ('dbg_jira_issue_metadata_jellyfish_addl', issue_metadata_addl_from_jellyfish),
            ('dbg_jira_issue_missing_issue_ids', missing_issue_ids),
            ('dbg_jira_issue_out_of_date_issue_ids', out_of_date_issue_ids),
            ('dbg_jira_issue_already_up_to_date_issue_ids', already_up_to_date_issue_ids),
        ]:
            try:
                write_file(config.outdir, fname, config.compress_output_files, vals)
            except Exception:
                logging_helper.send_to_agent_log_file(
                    f"Could not write issue metadata for file: {fname}", level=logging.ERROR
                )
                logging_helper.send_to_agent_log_file(traceback.format_exc(), level=logging.ERROR)

        @diagnostics.capture_timing()
        @logging_helper.log_entry_exit(logger)
        def download_and_write_issues():
            return download_and_write_streaming(
                config.outdir,
                'jira_issues',
                config.compress_output_files,
                generator_func=download_necessary_issues,
                generator_func_args=(
                    jira_connection,
                    issue_ids_to_download,
                    config.jira_include_fields,
                    config.jira_exclude_fields,
                    config.jira_issue_batch_size,
                    config.jira_issue_download_concurrent_threads,
                ),
                item_id_dict_key='id',
                addl_info_dict_key='key',
                batch_size=2000,
            )

        downloaded_issue_info = download_and_write_issues()

        issue_ids_needing_re_download = detect_issues_needing_re_download(
            downloaded_issue_info,
            issue_metadata_from_jellyfish,
            issue_metadata_addl_from_jellyfish,
        )

        @diagnostics.capture_timing()
        @logging_helper.log_entry_exit(logger)
        def download_and_write_issues_needing_re_download():
            return download_and_write_streaming(
                config.outdir,
                'jira_issues_re_downloaded',
                config.compress_output_files,
                generator_func=download_necessary_issues,
                generator_func_args=(
                    jira_connection,
                    list(issue_ids_needing_re_download),
                    config.jira_include_fields,
                    config.jira_exclude_fields,
                    config.jira_issue_batch_size,
                    config.jira_issue_download_concurrent_threads,
                ),
                item_id_dict_key='id',
                addl_info_dict_key='key',
                batch_size=2000,
            )

        re_downloaded_issue_info = download_and_write_issues_needing_re_download()

        all_downloaded_issue_ids = [
            int(i[0]) for i in chain(downloaded_issue_info, re_downloaded_issue_info)
        ]

        write_file(
            config.outdir,
            'jira_issue_ids_downloaded',
            config.compress_output_files,
            all_downloaded_issue_ids,
        )
        write_file(
            config.outdir,
            'jira_issue_ids_deleted',
            config.compress_output_files,
            list(deleted_issue_ids),
        )

        if config.jira_download_worklogs:
            write_file(
                config.outdir,
                'jira_worklogs',
                config.compress_output_files,
                download_worklogs(jira_connection, all_downloaded_issue_ids, endpoint_jira_info),
            )

        write_file(
            config.outdir,
            'jira_customfieldoptions',
            config.compress_output_files,
            download_customfieldoptions(jira_connection, project_ids),
        )

        write_file(
            config.outdir,
            'jira_statuses',
            config.compress_output_files,
            download_statuses(jira_connection),
        )
        try:
            write_file(
                config.outdir,
                'jira_teams',
                config.compress_output_files,
                download_teams(jira_connection),
            )
        except Exception as e:
            logging_helper.send_to_agent_log_file(
                f"Could not download teams, got {e}", level=logging.ERROR
            )

        return {'type': 'Jira', 'status': 'success'}

    except Exception as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[e], error_code=3002, exc_info=True
        )
        return {'type': 'Jira', 'status': 'failed'}
