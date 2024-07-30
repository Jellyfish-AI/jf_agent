from collections import namedtuple, defaultdict
import json
import logging
import math
import random
import re
import traceback
from typing import Dict

from dateutil import parser
from jira.exceptions import JIRAError
from jira.resources import dict2resource
from jira.utils import json_loads
import queue
import sys
import string
import threading
from tqdm import tqdm

from jf_agent.jf_jira.utils import retry_for_status
from jf_agent.util import split
from jf_ingest import diagnostics, logging_helper
from jf_ingest.utils import retry_for_status

logger = logging.getLogger(__name__)


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


# Returns an array of Resolutions dicts
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_resolutions(jira_connection):
    logger.info("downloading jira resolutions... [!n]")
    result = [r.raw for r in retry_for_status(jira_connection.resolutions)]
    logger.info("✓")
    return result


# Returns an array of IssueType dicts
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_issuetypes(jira_connection, project_ids):
    '''
    For Jira next-gen projects, issue types can be scoped to projects.
    For issue types that are scoped to projects, only extract the ones
    in the extracted projects.
    '''
    logger.info('downloading jira issue types...  [!n]',)
    result = []
    for it in retry_for_status(jira_connection.issue_types):
        if 'scope' in it.raw and it.raw['scope']['type'] == 'PROJECT':
            if it.raw['scope']['project']['id'] in project_ids:
                result.append(it.raw)
        else:
            result.append(it.raw)
    logger.info('✓')
    return result


# Returns an array of LinkType dicts
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_issuelinktypes(jira_connection):
    logger.info('downloading jira issue link types... [!n]')
    result = [lt.raw for lt in retry_for_status(jira_connection.issue_link_types)]
    logger.info('✓')
    return result


# Returns an array of Priority dicts
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_priorities(jira_connection):
    logger.info('downloading jira priorities... [!n]')
    result = [p.raw for p in retry_for_status(jira_connection.priorities)]
    logger.info('✓')
    return result


# Each project has a list of versions.
# Returns an array of Project dicts, where each one is agumented with an array of associated Version dicts.
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_projects_and_versions(
    jira_connection, include_projects, exclude_projects, include_categories, exclude_categories
):
    logger.info('downloading jira projects... [!n]')

    filters = []
    if include_projects:
        filters.append(lambda proj: proj.key in include_projects)
    if exclude_projects:
        filters.append(lambda proj: proj.key not in exclude_projects)
    if include_categories:

        def _include_filter(proj):
            # If we have a category-based allowlist and the project
            # does not have a category, do not include it.
            if not hasattr(proj, 'projectCategory'):
                return False

            return proj.projectCategory.name in include_categories

        filters.append(_include_filter)
    if exclude_categories:

        def _exclude_filter(proj):
            # If we have a category-based excludelist and the project
            # does not have a category, include it.
            if not hasattr(proj, 'projectCategory'):
                return True

            return proj.projectCategory.name not in exclude_categories

        filters.append(_exclude_filter)

    def project_is_accessible(project_id):
        try:
            retry_for_status(
                jira_connection.search_issues, f'project = {project_id}', fields=['id']
            )
            return True
        except JIRAError as e:
            # Handle zombie projects that appear in the project list
            # but are not actually accessible.  I don't know wtf Black
            # is doing with this formatting, but whatever.
            if (
                e.status_code == 400
                and e.text
                == f"A value with ID '{project_id}' does not exist for the field 'project'."
            ):
                logging_helper.log_standard_error(
                    logging.ERROR, msg_args=[project_id], error_code=2112,
                )
                return False
            else:
                raise

    all_projects = retry_for_status(jira_connection.projects)
    projects = [
        proj
        for proj in all_projects
        if all(filt(proj) for filt in filters) and project_is_accessible(proj.id)
    ]

    if not projects:
        raise Exception(
            'No Jira projects found that meet all the provided filters for project and project category. Aborting... '
        )

    logger.info('✓')

    logger.info('downloading jira project components... [!n]')
    for p in projects:
        p.raw.update(
            {'components': [c.raw for c in retry_for_status(jira_connection.project_components, p)]}
        )
    logger.info('✓')

    logger.info('downloading jira versions... [!n]')
    result = []
    for p in projects:
        versions = retry_for_status(jira_connection.project_versions, p)
        p.raw.update({'versions': [v.raw for v in versions]})
        result.append(p.raw)
    logger.info('✓')
    return result


# Boards and Sprints are many-to-many.
# Returns a 3-tuple:
#   - Array of board dicts
#   - Array of sprint dicts
#   - Array of board/sprint links
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_boards_and_sprints(jira_connection, project_ids, download_sprints):
    boards_by_id = {}  # De-dup by id, since the same board might come back from more than one query
    for project_id in tqdm(project_ids, desc='downloading jira boards...', file=sys.stdout):
        b_start_at = 0
        while True:
            try:
                # Can't use the jira_connection's .boards() method, since it doesn't support all the query parms
                project_boards = retry_for_status(
                    jira_connection._session.get,
                    url=f'{jira_connection._options["server"]}/rest/agile/1.0/board',
                    params={
                        'maxResults': 50,
                        'startAt': b_start_at,
                        'type': 'scrum',
                        'includePrivate': 'false',
                        'projectKeyOrId': project_id,
                    },
                ).json()['values']
            except JIRAError as e:
                if e.status_code == 400:
                    logging_helper.log_standard_error(
                        logging.ERROR, msg_args=[project_id], error_code=2202,
                    )
                    break
                raise

            if not project_boards:
                break

            b_start_at += len(project_boards)
            boards_by_id.update({board['id']: board for board in project_boards})

    links = []
    sprints = {}
    if download_sprints:
        for b in tqdm(boards_by_id.values(), desc='downloading jira sprints', file=sys.stdout):
            s_start_at = 0
            sprints_for_board = []
            while True:
                batch = None
                try:
                    batch = retry_for_status(
                        jira_connection.sprints,
                        # ignore future sprints
                        board_id=b['id'],
                        startAt=s_start_at,
                        maxResults=50,
                        state='active,closed',
                    )
                except JIRAError as e:
                    # JIRA returns 500 errors for various reasons: board is
                    # misconfigured; "falied to execute search"; etc.  Just
                    # skip and move on
                    if e.status_code == 500 or e.status_code == 404:
                        logger.info(f"Couldn't get sprints for board {b['id']}.  Skipping...")
                    elif e.status_code == 400:
                        logging_helper.log_standard_error(
                            logging.ERROR,
                            msg_args=[str(b), str(s_start_at), str(e)],
                            error_code=2203,
                            exc_info=True,
                        )
                    else:
                        raise

                if not batch:
                    break
                s_start_at += len(batch)
                sprints_for_board.extend(batch)

            links.append({'board_id': b['id'], 'sprint_ids': [s.id for s in sprints_for_board]})
            sprints.update({s.id: s for s in sprints_for_board})

    return list(boards_by_id.values()), [s.raw for s in sprints.values()], links


IssueMetadata = namedtuple('IssueMetadata', ('key', 'updated'))


def get_issues(jira_connection, issue_jql, start_at, batch_size):
    original_batch_size = batch_size
    error = None
    while batch_size > 0:
        try:
            api_response = retry_for_status(
                jira_connection.search_issues,
                f'{issue_jql} order by id asc',
                fields=['updated'],
                startAt=start_at,
                maxResults=batch_size,
            )
            return api_response
        except (JIRAError, KeyError) as e:
            if hasattr(e, 'status_code') and e.status_code < 500:
                # something wrong with our request; re-raise
                raise

            # We have seen sporadic server-side flakiness here. Sometimes Jira Server (but not
            # Jira Cloud as far as we've seen) will return a 200 response with an empty JSON
            # object instead of a JSON object with an "issues" key, which results in the
            # `search_issues()` function in the Jira library throwing a KeyError.
            #
            # Sometimes both cloud and server will return a 5xx.
            #
            # In either case, reduce the maxResults parameter and try again, on the theory that
            # a smaller ask will prevent the server from choking.
            batch_size = int(batch_size / 2)
            error = e
            logging_helper.log_standard_error(
                logging.WARNING, msg_args=[batch_size], error_code=3012,
            )

    # copied logic from jellyfish direct connect
    # don't bail, just skip
    # khardy 2023-03-16
    logging_helper.log_standard_error(
        logging.WARNING,
        msg_args=[f"{type(error)}", issue_jql, start_at, original_batch_size],
        error_code=3092,
    )

    return {}


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_all_issue_metadata(
    jira_connection, all_project_ids, earliest_issue_dt, num_parallel_threads, issue_filter
) -> Dict[int, IssueMetadata]:
    logger.info('downloading issue metadata... [!n]')

    # all_project_ids is passed in as a set - need it to be a list so it can be split
    # randomize order so that we don't always hit the same projects first
    all_project_ids = list(all_project_ids)
    random.shuffle(all_project_ids)

    # If project_ids is too long (Max URI is 26526) we need to do it in multiple GET requests
    # Set to 20K to be on the safe side
    # An issue id is 5 numbers long and the escaped comma is 3 characters
    # The actual url will be longer than the len_project_ids_string due to the escaped commas
    # and there is some other params in the url so add 2 for each comma and then add on 200
    # for extra params

    max_length = 20000
    len_project_ids_string = len(",".join(all_project_ids)) + len(all_project_ids) * 2 + 200
    num_pulls = len_project_ids_string // max_length + 1

    project_ids_array = []
    if num_pulls == 1:
        project_ids_array.append(all_project_ids)
    else:
        # Over 20K characters - break up project_ids evenly based on num_pulls
        n = len(all_project_ids) // num_pulls
        project_ids_array = [all_project_ids[i : i + n] for i in range(0, len(all_project_ids), n)]

    all_issue_metadata: Dict[int, IssueMetadata] = {}
    for project_ids in project_ids_array:
        issue_jql = (
            f'project in ({",".join(project_ids)}) and updatedDate > '
            f'{"0" if not earliest_issue_dt else earliest_issue_dt.strftime("%Y-%m-%d")}'
        )
        if issue_filter:
            issue_jql += f' and {issue_filter}'
        total_num_issues = retry_for_status(
            jira_connection.search_issues, issue_jql, fields=['id']
        ).total
        issues_per_thread = math.ceil(total_num_issues / num_parallel_threads)

        thread_exceptions = [None] * num_parallel_threads

        def _download_some(thread_num, start_at, end_at):
            batch_size = 1000
            try:
                while start_at < min(end_at, total_num_issues):
                    api_resp = get_issues(jira_connection, issue_jql, start_at, batch_size)

                    issue_metadata = {
                        int(iss.id): IssueMetadata(iss.key, parser.parse(iss.fields.updated))
                        for iss in api_resp
                    }
                    all_issue_metadata.update(issue_metadata)
                    if len(issue_metadata) == 0:
                        start_at += 1  # nothing came back, so jump 1 issue ahead and hopefully skip the problem
                    else:
                        start_at += len(issue_metadata)

            except Exception as e:
                thread_exceptions[thread_num] = e
                logging_helper.log_standard_error(
                    logging.ERROR, msg_args=[thread_num, traceback.format_exc()], error_code=3032,
                )

        threads = [
            threading.Thread(
                target=_download_some, args=[i, i * issues_per_thread, (i + 1) * issues_per_thread]
            )
            for i in range(num_parallel_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if any(thread_exceptions):
            any_exception = [e for e in thread_exceptions if e][0]
            raise Exception('Some thread(s) threw exceptions') from any_exception

    logger.info('✓')

    return all_issue_metadata


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def detect_issues_needing_sync(
    issue_metadata_from_jira: Dict[int, IssueMetadata],
    issue_metadata_from_jellyfish: Dict[int, IssueMetadata],
):
    missing_issue_ids = set()
    already_up_to_date_issue_ids = set()
    out_of_date_issue_ids = set()
    deleted_issue_ids = set()

    for k in issue_metadata_from_jira.keys():
        if k not in issue_metadata_from_jellyfish:
            missing_issue_ids.add(k)
        else:
            if issue_metadata_from_jira[k].updated != issue_metadata_from_jellyfish[k].updated:
                out_of_date_issue_ids.add(k)
            else:
                already_up_to_date_issue_ids.add(k)

    deleted_issue_ids = issue_metadata_from_jellyfish.keys() - issue_metadata_from_jira.keys()

    return missing_issue_ids, already_up_to_date_issue_ids, out_of_date_issue_ids, deleted_issue_ids


def detect_issues_needing_re_download(
    downloaded_issue_info, issue_metadata_from_jellyfish, issue_metadata_addl_from_jellyfish
):
    issue_keys_changed = []
    for issue_id_str, issue_key in downloaded_issue_info:
        existing_metadata = issue_metadata_from_jellyfish.get(int(issue_id_str))
        if existing_metadata and issue_key != existing_metadata.key:
            logger.info(
                f'Detected a key change for issue {issue_id_str} ({existing_metadata.key} -> {issue_key})'
            )
            issue_keys_changed.append(existing_metadata.key)

    issues_by_elfik, issues_by_pfik = defaultdict(list), defaultdict(list)
    for issue_id, (elfik, pfik) in issue_metadata_addl_from_jellyfish.items():
        if elfik:
            issues_by_elfik[elfik].append(issue_id)
        if pfik:
            issues_by_pfik[pfik].append(issue_id)

    # Find all of the issues that refer to those issues through epic_link_field_issue_key
    # or parent_field_issue_key; these issues need to be re-downloaded
    issue_ids_needing_re_download = set()
    for changed_key in issue_keys_changed:
        issue_ids_needing_re_download.update(set(issues_by_elfik.get(changed_key, [])))
        issue_ids_needing_re_download.update(set(issues_by_pfik.get(changed_key, [])))

    return issue_ids_needing_re_download


def download_necessary_issues(
    jira_connection,
    issue_ids_to_download,
    include_fields,
    exclude_fields,
    suggested_batch_size,
    num_parallel_threads,
):
    '''
    A generator that yields batches of issues, until we've downloaded all of the issues given by
    issue_ids_to_download
    '''
    if not issue_ids_to_download:
        return

    field_spec = list(include_fields) or ['*all']
    field_spec.extend(f'-{field}' for field in exclude_fields)

    actual_batch_size = retry_for_status(
        jira_connection.search_issues,
        'order by id asc',
        fields=field_spec,
        expand='renderedFields,changelog',
        startAt=0,
        maxResults=suggested_batch_size,
    ).maxResults

    num_threads_to_use = min(
        math.ceil(len(issue_ids_to_download) / actual_batch_size), num_parallel_threads
    )
    random.shuffle(issue_ids_to_download)
    issue_ids_for_threads = list(split(issue_ids_to_download, num_threads_to_use))

    # Make threads to talk to Jira and write batches of issues to the queue
    q = queue.Queue()
    threads = [
        threading.Thread(
            target=_download_jira_issues_segment,
            args=[
                thread_num,
                jira_connection,
                issue_ids_for_threads[thread_num],
                field_spec,
                actual_batch_size,
                q,
            ],
        )
        for thread_num in range(num_threads_to_use)
    ]
    for t in threads:
        t.start()

    encountered_issue_ids = set()
    with tqdm(
        desc='downloading jira issues', total=len(issue_ids_to_download), file=sys.stdout
    ) as prog_bar:
        # Read batches from queue
        finished = 0
        while finished < len(threads):
            batch = q.get()
            if batch is None:
                # found the marker that a thread is done
                finished += 1
                continue

            if isinstance(batch, BaseException):
                # thread had a problem; rethrow
                raise batch

            # issues sometimes appear at the end of one batch and again at the beginning of the next; de-dup them
            new_issues_this_batch = []
            for issue_id, issue in batch:
                if issue_id not in encountered_issue_ids:
                    encountered_issue_ids.add(issue_id)
                    new_issues_this_batch.append(issue)
            prog_bar.update(len(new_issues_this_batch))

            yield _filter_changelogs(new_issues_this_batch, include_fields, exclude_fields)

    for t in threads:
        t.join()


# The Jira API can sometimes include fields in the changelog that
# were excluded from the list of fields. Strip them out.
def _filter_changelogs(issues, include_fields, exclude_fields):
    def _strip_history_items(items):
        # Skip items that are a change of a field that's filtered out
        for i in items:
            field_id_field = _get_field_identifier(i)
            if not field_id_field:
                logging_helper.log_standard_error(
                    level=logging.WARNING, error_code=3082, msg_args=[i.keys()],
                )
            if include_fields and i.get(field_id_field) not in include_fields:
                continue
            if i.get(field_id_field) in exclude_fields:
                continue
            yield i

    def _get_field_identifier(item) -> str:
        return 'fieldId' if 'fieldId' in item else 'field' if 'field' in item else None

    def _strip_changelog_histories(histories):
        # omit any histories that, when filtered, have no items.  ie, if
        # a user only changed fields that we've stripped, cut out that
        # history record entirely
        for h in histories:
            stripped_items = list(_strip_history_items(h['items']))
            if stripped_items:
                yield {**h, 'items': stripped_items}

    def _strip_changelog(c):
        # copy a changelog, stripping excluded fields from the history
        return {**c, 'histories': list(_strip_changelog_histories(c['histories']))}

    return [{**i, 'changelog': _strip_changelog(i['changelog'])} for i in issues]


@logging_helper.log_entry_exit(logger)
def _download_jira_issues_segment(
    thread_num, jira_connection, jira_issue_ids_segment, field_spec, batch_size, q
):
    '''
    Each thread's target function.  Downloads 1/nth of the issues necessary, where
    n is the number of threads, a page at a time.  Puts the result of each page's
    download onto the shared queue.
    '''
    start_at = 0
    logging_helper.send_to_agent_log_file(
        f"Beginning to download jira issues in segment of {len(jira_issue_ids_segment)}"
    )
    try:
        while start_at < len(jira_issue_ids_segment):
            issues, num_apparently_deleted = _download_jira_issues_page(
                jira_connection, jira_issue_ids_segment, field_spec, start_at, batch_size
            )

            issues_retrieved = len(issues) + num_apparently_deleted
            start_at += issues_retrieved
            if issues_retrieved == 0:
                break

            rows_to_insert = [(int(issue['id']), issue) for issue in issues]

            # TODO: configurable way to scrub things out of raw_issues here before we write them out.
            q.put(rows_to_insert)

        # sentinel to mark that this thread finished
        q.put(None)

    except BaseException as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[thread_num], error_code=3042, exc_info=True,
        )
        q.put(e)


def _download_jira_issues_page(
    jira_connection, jira_issue_ids_segment, field_spec, start_at, batch_size
):
    '''
    Returns a tuple: (issues_downloaded, num_issues_apparently_deleted)
    '''
    get_changelog = True

    while batch_size > 0:
        ids_to_get = [str(iid) for iid in jira_issue_ids_segment[start_at : start_at + batch_size]]
        search_params = {
            'jql': f"id in ({','.join(ids_to_get)}) order by id asc",
            'fields': field_spec,
            'expand': ['renderedFields'],
            'startAt': 0,
            'maxResults': batch_size,
        }
        if get_changelog:
            search_params['expand'].append('changelog')

        try:
            resp_json = json_loads(
                retry_for_status(
                    jira_connection._session.post,
                    url=jira_connection._get_url('search'),
                    data=json.dumps(search_params),
                )
            )
            return _expand_changelog(resp_json['issues'], jira_connection), 0

        except (json.decoder.JSONDecodeError, JIRAError) as e:
            if hasattr(e, 'status_code') and e.status_code == 429:
                # This is rate limiting ("Too many requests")
                raise

            batch_size = int(batch_size / 2)
            logging_helper.log_standard_error(
                logging.WARNING, msg_args=[e, batch_size], error_code=3052, exc_info=True,
            )
            if batch_size == 0:
                if re.match(r"A value with ID .* does not exist for the field 'id'", e.text):
                    return [], 1
                elif not get_changelog:
                    agent_logging.log_and_print_error_or_warning(
                        logger, logging.WARNING, msg_args=[search_params], error_code=3062,
                    )
                    return [], 0
                else:
                    get_changelog = False
                    batch_size = 1


# Sometimes the results of issue search has an incomplete changelog.  Fill it in if so.
def _expand_changelog(jira_issues, jira_connection):
    for i in jira_issues:
        changelog = getattr(i, 'changelog', None)
        if changelog and changelog.total != changelog.maxResults:
            # expand the changelog
            start_at = changelog.maxResults
            batch_size = 100
            while start_at < changelog.total:
                more_cls = retry_for_status(
                    jira_connection._get_json,
                    f'issue/{i["id"]}/changelog',
                    {'startAt': start_at, 'maxResults': batch_size},
                )['values']
                changelog.histories.extend(dict2resource(i) for i in more_cls)
                i['changelog']['histories'].extend(more_cls)
                start_at += len(more_cls)
    return jira_issues


# Returns a dict with two items: 'existing' gives a list of all worklogs
# that currently exist; 'deleted' gives the list of worklogs that
# existed at some point previously, but have since been deleted
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
# TODO make this happen incrementally -- only pull down the worklogs that have been updated
# more recently than we've already stored
def download_worklogs(jira_connection, issue_ids, endpoint_jira_info):
    logger.info('downloading jira worklogs...  [!n]')
    updated = []
    since = endpoint_jira_info.get('last_updated', 0)
    while True:
        worklog_ids_json = retry_for_status(
            jira_connection._get_json, 'worklog/updated', params={'since': since}
        )
        updated_worklog_ids = [v['worklogId'] for v in worklog_ids_json['values']]

        resp = retry_for_status(
            jira_connection._session.post,
            url=jira_connection._get_url('worklog/list'),
            data=json.dumps({'ids': updated_worklog_ids}),
        )
        try:
            worklog_list_json = json_loads(resp)
        except ValueError:
            logger.info("Couldn't parse JIRA response as JSON: %s", resp.text)
            raise

        updated.extend([wl for wl in worklog_list_json if int(wl['issueId']) in issue_ids])
        if worklog_ids_json['lastPage']:
            break
        since = worklog_ids_json['until']

    logger.info('✓')

    return {'existing': updated, 'deleted': []}


# Returns an array of CustomFieldOption items
@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_customfieldoptions(jira_connection, project_ids):
    logger.info('downloading jira custom field options... [!n]')
    optionvalues = {}
    for project_id in project_ids:
        try:
            meta = retry_for_status(
                jira_connection.createmeta,
                projectIds=[project_id],
                expand='projects.issuetypes.fields',
            )
        except JIRAError:
            logging_helper.log_standard_error(logging.WARNING, error_code=3072, exc_info=False)
            return []

        # Custom values are buried deep in the createmeta response:
        #     projects -> issuetypes -> fields -> allowedValues
        for project in meta['projects']:
            for issue_type in project['issuetypes']:
                for field_key, field in issue_type['fields'].items():
                    if 'key' in field:
                        field_key = field['key']
                    # same field may end up in multiple issue types (bug, task, etc),
                    # so check if we've already added it
                    if field_key not in optionvalues and _is_option_field(field):
                        optionvalues[field_key] = field['allowedValues']

    result = [{'field_key': k, 'raw_json': v} for k, v in optionvalues.items()]
    logger.info('✓')
    return result


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_teams(jira_connection):
    logger.info('downloading jira teams... [!n]')
    server = jira_connection._options['server']
    try:
        teams_url = jira_connection.JIRA_BASE_URL.format(
            server=server, rest_path='teams-api', rest_api_version='1.0', path='team'
        )
        teams = retry_for_status(jira_connection._get_json, 'team', base=teams_url)
        logger.info('✓')
        return teams
    except Exception as e:
        logging_helper.send_to_agent_log_file(
            f"Could not fetch teams, instead got {e}", level=logging.ERROR
        )
        return ""


@diagnostics.capture_timing()
@logging_helper.log_entry_exit(logger)
def download_statuses(jira_connection):
    logger.info('downloading jira statuses... [!n]')
    statuses = retry_for_status(jira_connection.statuses)
    result = [{'status_id': status.id, 'raw_json': status.raw} for status in statuses]
    logger.info('✓')
    return result


# return True if this field is either a single or multi select option
# field_meta is a dict that comes from raw field json
def _is_option_field(field_meta):
    schema = field_meta['schema']
    is_option_field = schema['type'] == 'option' or (
        'items' in schema and schema['items'] == 'option'
    )
    return is_option_field


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
    from jf_agent.jf_jira import get_basic_jira_connection
    from jf_agent.git import get_git_client

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
                        logging.ERROR, error_code=2122,
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
