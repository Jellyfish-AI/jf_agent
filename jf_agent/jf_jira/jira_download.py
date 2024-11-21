import logging
import string
import traceback
from typing import Optional

from jf_ingest import diagnostics, logging_helper
from jf_ingest.config import IngestionConfig
from jf_ingest.jf_jira import load_and_push_jira_to_s3
from jf_ingest.jf_jira.auth import get_jira_connection as get_jira_connection_from_jf_ingest
from jf_ingest.jf_jira.custom_fields import JCFVUpdateFullPayload, identify_custom_field_mismatches
from jf_ingest.utils import retry_for_status
from jira.exceptions import JIRAError

from jf_agent.config_file_reader import ValidatedConfig
from jf_agent.jf_jira.utils import retry_for_status

logger = logging.getLogger(__name__)

MAKARA_JIRA_SVR_THREAD_COUNT_FLAG = 'makara-jira-server-dc-thread-count'


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def run_jira_download(config: ValidatedConfig, ingest_config: IngestionConfig) -> dict:
    from jf_agent.jf_jira import print_all_jira_fields

    logger.info(
        'Running Jira Download...',
    )
    download_status = 'success'

    # Not really sure what this print_all_jira_fields is or who still uses it.
    # Leaving it in for backwards compatibility
    jira_connection = get_jira_connection_from_jf_ingest(ingest_config.jira_config)
    if config.run_mode_is_print_all_jira_fields:
        print_all_jira_fields(config, jira_connection)

    try:
        ids_to_redownload = detect_and_repair_custom_fields(ingest_config=ingest_config)
        if ids_to_redownload:
            count_of_ids_to_redownload_previously = len(
                ingest_config.jira_config.jellyfish_issue_ids_for_redownload
            )
            ingest_config.jira_config.jellyfish_issue_ids_for_redownload.update(ids_to_redownload)
            count_of_additional_ids_to_redownload = (
                len(ingest_config.jira_config.jellyfish_issue_ids_for_redownload)
                - count_of_ids_to_redownload_previously
            )
            logging_helper.send_to_agent_log_file(
                f'Detect and Repair Custom Fields found {len(ids_to_redownload)} to redownload, '
                f'which gave us an additional {count_of_additional_ids_to_redownload} unique IDs to redownload'
            )
            logger.info('Detect and repair custom fields completed successfully')
    except Exception as e:
        logger.warning(
            f'Exception {e} encountered when attempting to run {detect_and_repair_custom_fields.__name__}.'
        )
        logging_helper.send_to_agent_log_file(traceback.format_exc())

    try:
        logger.info(f'Attempting to use JF Ingest for Jira Ingestion')
        success = load_and_push_jira_to_s3(ingest_config)
        download_status = 'success' if success else 'failed'
        logger.info(
            'Jira Download Complete',
        )
    except Exception as e:
        download_status = 'failed'
        logger.error(
            'Error encountered when downloading Jira data. '
            'This Jira submission will be marked as failed. '
            f'Error: {e}'
        )
        logging_helper.send_to_agent_log_file(traceback.format_exc(), level=logging.ERROR)
    finally:
        return {'type': 'Jira', 'status': download_status}


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def detect_and_repair_custom_fields(
    ingest_config: IngestionConfig, submit_issues_for_repair: Optional[bool] = True
) -> Optional[set[str]]:
    jira_connection = get_jira_connection_from_jf_ingest(ingest_config.jira_config)
    deployment_type = jira_connection.server_info()['deploymentType']
    use_adaptive_throttler = False

    # Default thread count of 10
    thread_count = 10

    # Enable use of adaptive throttler for Jira server/dc
    if deployment_type != 'Cloud':
        # If issue_download_concurrent_threads is set in the config, use that number of threads
        # Otherwise, use the feature flag to determine thread count for Jira server/dc
        # Default thread count of 5 if the feature flag is not set or unavailable
        thread_count = (
            ingest_config.jira_config.issue_download_concurrent_threads
            if ingest_config.jira_config.issue_download_concurrent_threads
            else ingest_config.jira_config.feature_flags.get(MAKARA_JIRA_SVR_THREAD_COUNT_FLAG, 5)
        )

        logger.info(f'Using adaptive throttler with {thread_count} threads for custom field repair')
        use_adaptive_throttler = True

    jcfv_update_payload: JCFVUpdateFullPayload = identify_custom_field_mismatches(
        ingest_config,
        nthreads=thread_count,
        mark_for_redownload=submit_issues_for_repair,
        use_throttler=use_adaptive_throttler,
    )

    if submit_issues_for_repair:
        missing_db_ids = [jcfv.jira_issue_id for jcfv in jcfv_update_payload.missing_from_db_jcfv]
        missing_jira_ids = [
            jcfv.jira_issue_id for jcfv in jcfv_update_payload.missing_from_jira_jcfv
        ]
        missing_out_of_sync_ids = [
            jcfv.jira_issue_id for jcfv in jcfv_update_payload.out_of_sync_jcfv
        ]
        all_ids = set(missing_db_ids + missing_jira_ids + missing_out_of_sync_ids)
        return set([str(id) for id in all_ids])


# Returns an array of User dicts
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_users(
    jira_connection, gdpr_active, quiet=False, required_email_domains=None, is_email_required=False
):
    if not quiet:
        logger.info('downloading jira users...  [!n]')

    jira_users = _search_all_users(jira_connection, gdpr_active)

    # Some jira instances won't return more than one page of
    # results.  If we have seen approximately 1000 results, try
    # searching a different way
    if 950 <= len(jira_users) <= 1000:
        logger.info(
            f'Page limit reached with {len(jira_users)} users, falling back to search by letter method.'
        )
        jira_users = _users_by_letter(jira_connection, gdpr_active)

    if len(jira_users) == 0:
        raise RuntimeError(
            'The agent is unable to see any users. Please verify that this user has the "browse all users" permission.'
        )

    if required_email_domains:

        def _get_email_domain(email: str):
            try:
                return email.split("@")[1]
            except AttributeError:
                return ""
            except IndexError:
                return ""

        filtered_users = []
        for user in jira_users:
            try:
                email = user['emailAddress']
                email_domain = _get_email_domain(email)
                if email_domain in required_email_domains:
                    filtered_users.append(user)
            except KeyError:
                if is_email_required:
                    filtered_users.append(user)

        jira_users = filtered_users

    if not quiet:
        logger.info('✓')
    return jira_users


# Returns an array of Field dicts
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_fields(jira_connection, include_fields, exclude_fields):
    logger.info('downloading jira fields... [!n]')

    filters = []
    if include_fields:
        filters.append(lambda field: field['id'] in include_fields)
    if exclude_fields:
        filters.append(lambda field: field['id'] not in exclude_fields)

    fields = [
        field
        for field in retry_for_status(jira_connection.fields)
        if all(filt(field) for filt in filters)
    ]

    logger.info('✓')
    return fields


def _jira_user_key(user_dict):
    return user_dict.get('key', user_dict.get('accountId', 'unknown_key'))


def _search_all_users(jira_connection, gdpr_active):
    jira_users = {}
    start_at = 0

    # different jira versions / different times, the way to list all users has changed. Try a few.
    for q in [' ', '""', '%', '@']:
        logging_helper.send_to_agent_log_file(f'Attempting wild card search with {q}')
        # iterate through pages of results
        while True:
            users = _search_users(
                jira_connection, gdpr_active, query=q, start_at=start_at, include_inactive=True
            )
            if not users:
                # we're done, no more pages
                break

            # add this page of results and get the next page
            jira_users.update({_jira_user_key(u): u for u in users})
            start_at += len(users)

        # found users; no need to try other query techniques
        if jira_users:
            logging_helper.send_to_agent_log_file(f'Found {len(jira_users.keys())} users')
            return list(jira_users.values())

    # no users found
    return []


def _users_by_letter(jira_connection, gdpr_active):
    jira_users = {}
    for letter in string.ascii_lowercase:
        jira_users.update(
            {
                _jira_user_key(u): u
                for u in _search_users(
                    jira_connection,
                    gdpr_active,
                    query=f'{letter}.',
                    include_inactive=True,
                    include_active=False,
                )
            }
        )
        jira_users.update(
            {
                _jira_user_key(u): u
                for u in _search_users(
                    jira_connection,
                    gdpr_active,
                    query=f'{letter}.',
                    include_inactive=False,
                    include_active=True,
                )
            }
        )
    return list(jira_users.values())


def _search_users(
    jira_connection,
    gdpr_active,
    query,
    start_at=0,
    max_results=1000,
    include_active=True,
    include_inactive=False,
):
    if query is None:
        # use new endpoint that doesn't take a query.  This may not exist in some instances.
        try:
            return retry_for_status(
                jira_connection._get_json,
                'users/search',
                {'startAt': start_at, 'maxResults': max_results},
            )
        except JIRAError:
            return []

    if gdpr_active:
        # jira_connection.search_users creates a query that is no longer accepted on
        # GDPR-compliant Jira instances.  Construct the right query by hand
        params = {
            'startAt': start_at,
            'maxResults': max_results,
            'query': query,
            'includeActive': include_active,
            'includeInactive': include_inactive,
        }
        return retry_for_status(jira_connection._get_json, 'user/search', params)

    return [
        u.raw
        for u in retry_for_status(
            jira_connection.search_users,
            query,
            startAt=start_at,
            maxResults=max_results,
            includeInactive=include_inactive,
            includeActive=include_active,
        )
    ]


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_missing_repos_found_by_jira(config, creds, issues_to_scan):
    from jf_agent.git import get_git_client
    from jf_agent.jf_jira import get_basic_jira_connection

    # obtain list of repos in jira
    jira_connection = get_basic_jira_connection(config, creds)
    missing_repositories = _get_repos_list_in_jira(issues_to_scan, jira_connection)

    # cross-reference them with git sources
    is_multi_git_config = len(config.git_configs) > 1
    for git_config in config.git_configs:
        logger.info(
            f'Checking against the {git_config.git_provider} instance {git_config.git_instance_slug} ..'
        )
        if is_multi_git_config:
            instance_slug = git_config.git_instance_slug
            instance_creds = creds.git_instance_to_creds.get(instance_slug)
        else:
            # support legacy single-git support, which assumes only one available git instance
            instance_creds = list(creds.git_instance_to_creds.values())[0]

        git_connection = get_git_client(
            config=git_config,
            git_creds=instance_creds,
            skip_ssl_verification=config.skip_ssl_verification,
        )
        _remove_repos_present_in_git_instance(git_connection, git_config, missing_repositories)

    # return the final list of missing repositories
    result = [{'name': k, 'url': v['url']} for k, v in missing_repositories.items()]
    return result


def _get_repos_list_in_jira(issues_to_scan, jira_connection):
    logger.info('Scanning Jira issues for Git repos...')
    missing_repositories = {}

    for issue_id, instance_types in issues_to_scan.items():
        for instance_type in instance_types:
            try:
                repositories = _scan_jira_issue_for_repo_data(
                    jira_connection, issue_id, instance_type
                )
            except JIRAError as e:
                if e.status_code == 403:
                    logging_helper.log_standard_error(
                        logging.ERROR,
                        error_code=2122,
                    )
                    return []

            for repo in repositories:
                repo_name = repo['name']
                repo_url = repo['url']

                if repo_name not in missing_repositories:
                    missing_repositories[repo_name] = {
                        'name': repo_name,
                        'url': repo_url,
                        'instance_type': instance_type,
                    }
        return missing_repositories


def _remove_repos_present_in_git_instance(git_connection, git_config, missing_repositories):
    from jf_agent.git import get_repos_from_git

    try:
        # Cross reference any apparently missing repos found by Jira with actual Git repo sources since
        # Jira may return inexact repo names/urls. Remove any mismatches found.
        _remove_mismatched_repos(
            missing_repositories, get_repos_from_git(git_connection, git_config), git_config
        )
    except Exception as e:
        logger.info(
            f'WARNING: Got an error when trying to cross-reference repos discovered by '
            f'Jira with Git repos: {e}\nSkipping this process and returning all repos '
            f'discovered by Jira...'
        )


def _scan_jira_issue_for_repo_data(jira_connection, issue_id, application_type):
    params = {
        'issueId': issue_id,
        'dataType': 'repository',
        'applicationType': application_type,
    }

    try:
        response = retry_for_status(
            jira_connection._get_json,
            '/rest/dev-status/1.0/issue/detail',
            params,
            base='{server}{path}',
        )
    except JIRAError as e:
        if e.status_code == 400:
            logger.info(
                f"WARNING: received a 400 when requesting development details for issue {issue_id}, "
                f"likely because it doesn't exist anymore -- skipping"
            )
            return []
        raise
    except Exception as e:
        logger.info(
            f'WARNING: caught {type(e)} exception requesting development details for '
            f'{issue_id} -- skipping'
        )
        return []

    if response.get('errors'):
        logger.info(
            f"WARNING: received an error when requesting development details for {issue_id}: {response['errors']}"
        )

    detail = response.get('detail', [])
    if not detail:
        logger.info(f'found no development details for {issue_id}')
        return []

    return detail[0]['repositories']


def _remove_mismatched_repos(repos_found_by_jira, git_repos, config):
    '''
    Cross reference results with data from git to differentiate a 'missing repo' from a 'missed match'
    '''
    if not (repos_found_by_jira and git_repos):
        return

    logger.info('comparing repos found by Jira against Git repos to find missing ones...')

    git_repo_names = []
    git_repo_urls = []
    for standardized_repo in git_repos:
        git_repo_names.extend([standardized_repo.get('full_name'), standardized_repo.get('name')])
        git_repo_urls.append(standardized_repo.get('url'))

    ignore_repos = []
    for repo in list(repos_found_by_jira.values()):
        repo_name = repo['name']
        repo_url = repo['url']

        # Jira sometimes responds with a different version of names/urls than
        # what is returned from their respective git APIs. In order to cross reference
        # the git repos given to us, we will try to query with something more similar:
        # (url ex.) gitlab.com/group/@group/@subgroup/@myrepo >> gitlab.com/group/subgroup/myrepo
        # (name ex.) @group@subgroup@myrepo >> myrepo
        if '@' in repo_url:
            repo_url = repo_url.replace('@', '/')
            repo_url = repo_url.split('/')
            seen = set()
            repo_url = [x for x in repo_url if not (x in seen or seen.add(x))]
            repo_url = '/'.join(repo_url)
        if '@' in repo_name:
            repo_name = repo_name.split('@')[-1]

        if repo_url in git_repo_urls or repo_name in git_repo_names:
            ignore_repos.append((repo['name'], repo['url']))
            del repos_found_by_jira[repo['name']]

    if len(ignore_repos):
        logger.info(
            '\nJira found the following repos but per your config file, Jellyfish already has access:'
        )
        for repo in ignore_repos:
            logger.info(f"* {repo[0]:30}\t{repo[1]}")
