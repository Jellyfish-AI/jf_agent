from tqdm import tqdm
import queue
import threading
import sys
import json
import logging

from jira.resources import dict2resource
from jira.exceptions import JIRAError
from jira.utils import json_loads


logger = logging.getLogger(__name__)


# Returns an array of User dicts
def download_users(jira_connection):
    print('downloading users... ', end='', flush=True)
    
    jira_users = set()
    start_at = 0
    while True:
        # Search for '%' or '@' so we get results from cloud or
        # on-prem, the latter being what on-prem appears to use as
        # a wildcard
        users = jira_connection.search_users('%', maxResults=1000, startAt=start_at, includeInactive=True) or \
                jira_connection.search_users('@', maxResults=1000, startAt=start_at, includeInactive=True)

        if not users:
            # Some jira instances won't return more than one page of
            # results.  If we have seen exactly 1000 results, try
            # searching a different way
            result = [u.raw for u in (jira_users if len(jira_users) != 1000 else _users_by_letter(jira_connection))]
            print('✓')
            return result

        jira_users.update(users)
        start_at += len(users)


# Returns an array of Field dicts
def download_fields(jira_connection):
    print('downloading fields... ', end='', flush=True)
    result = jira_connection.fields()
    print('✓')
    return result


# Returns an array of Resolutions dicts
def download_resolutions(jira_connection):
    print('downloading resolutions... ', end='', flush=True)
    result = [r.raw for r in jira_connection.resolutions()]
    print('✓')
    return result


# Returns an array of IssueType dicts
def download_issuetypes(jira_connection):
    print('downloading issue types... ', end='', flush=True)
    result = [it.raw for it in jira_connection.issue_types()]
    print('✓')
    return result


# Returns an array of LinkType dicts
def download_issuelinktypes(jira_connection):
    print('downloading issue link types... ', end='', flush=True)
    result = [lt.raw for lt in jira_connection.issue_link_types()]
    print('✓')
    return result


# Returns an array of Priority dicts
def download_priorities(jira_connection):
    print('downloading priorities... ', end='', flush=True)
    result = [p.raw for p in jira_connection.priorities()]
    print('✓')
    return result


# Each project has a list of version.
# Returns an array of Project dicts, where each one is agumented with an array of associated Version dicts.
def download_projects_and_versions(jira_connection):
    print('downloading projects... ', end='', flush=True)
    projects = jira_connection.projects()
    print('✓')

    print('downloading versions... ', end='', flush=True)
    result = [
        p.raw.update({'versions': [v.raw for v in jira_connection.project_versions(p)]}) or p.raw
        for p in projects
    ]
    print('✓')
    return result


# Boards and Sprints are many-to-many.
# Returns a 3-tuple:
#   - Array of board dicts
#   - Array of sprint dicts
#   - Array of board/sprint links
def download_boards_and_sprints(jira_connection):
    b_start_at = 0
    boards = []
    print('downloading boards... ', end='', flush=True)
    while True:
        # boards seem to come back in batches of 50 at most
        jira_boards = jira_connection.boards(startAt=b_start_at, maxResults=50)
        if not jira_boards:
            break
        b_start_at += len(jira_boards)
        boards.extend(jira_boards)
    print('✓')

    
    print('downloading sprints... ', end='', flush=True)
    links = []
    sprints = []
    for b in boards:
        if b.raw['type'] != 'scrum':
            continue
        s_start_at = 0
        sprints_for_board = []
        while True:
            batch = None
            try:
                batch = jira_connection.sprints(board_id=b.id, startAt=s_start_at, maxResults=50)
            except JIRAError as e:
                # JIRA returns 500 errors for various reasons: board is
                # misconfigured; "falied to execute search"; etc.  Just
                # skip and move on
                if e.status_code == 500:
                    logger.exception(f"Couldn't get sprints for board {b.id}.  Skipping...")
                else:
                    raise
            if not batch:
                break
            s_start_at += len(batch)
            sprints_for_board.extend(batch)
        
        links.append({'board_id': b.id,
                      'sprint_ids': [s.id for s in sprints_for_board]})
        sprints.extend(sprints_for_board)
    print('✓')
    
    return [b.raw for b in boards], [s.raw for s in sprints], links
        
def download_issues(jira_connection):
    issue_count = jira_connection.search_issues('created is not empty order by id asc', fields='id', maxResults=1).total
    parallel_threads = 10
    issues_per_thread = -(-issue_count // parallel_threads)

    # Make threads to talk to Jira and write batches of issues to the queue
    q = queue.Queue()
    threads = [threading.Thread(target=_download_some_jira_issues,
                                args=[i, jira_connection, i*issues_per_thread, (i+1)*issues_per_thread, q])
                   for i in range(parallel_threads)]
    for t in threads:
        t.start()

    issues = {}
    with tqdm(desc='downloading issues', total=issue_count, file=sys.stdout) as prog_bar:
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

            # issues sometimes appear at the end of one batch and again at the beginning of the next; use
            # the dict to dedupe
            old_count = len(issues)
            issues.update({i['id']: i for i in batch})
            new_count= len(issues) - old_count
            
            prog_bar.update(new_count)

    for t in threads:
        t.join()
        
    return list(issues.values())


# Returns a dict with two items: 'existing' gives a list of all worklogs
# that currently exist; 'deleted' gives the list of worklogs that
# existed at some point previously, but have since been deleted
def download_worklogs(jira_connection):
    print(f'downloading worklogs... ', end='', flush=True)
    updated = []
    while True:
        worklog_ids_json = jira_connection._get_json('worklog/updated', params={'since': 0})
        updated_worklog_ids = [v['worklogId'] for v in worklog_ids_json['values']]

        resp = jira_connection._session.post(
            url=jira_connection._get_url('worklog/list'),
            data=json.dumps({'ids': updated_worklog_ids})
        )
        try:
            worklog_list_json = json_loads(resp)
        except ValueError as e:
            logger.exception("Couldn't parse JIRA response as JSON: %s", resp.text)
            raise e

        updated.extend(worklog_list_json)
        if worklog_ids_json['lastPage']:
            break
    print('✓')

    print(f'Finding old worklogs that have been deleted... ', end='', flush=True)
    deleted = []
    while True:
        worklog_ids_json = jira_connection._get_json('worklog/deleted', params={'since': 0})
        deleted.extend(worklog_ids_json['values'])
        if worklog_ids_json['lastPage']:
            break

    print('✓')

    return {
        'existing': updated,
        'deleted': deleted,
    }


def _users_by_letter(jira_connection):
    jira_users = set()
    for letter in string.ascii_lowercase:
        jira_users.update(jira_connection.search_users(f'{letter}.', maxResults=1000, includeInactive=True, includeActive=False))
        jira_users.update(jira_connection.search_users(f'{letter}.', maxResults=1000, includeInactive=False, includeActive=True))
    return jira_users


def _download_some_jira_issues(i, jira_connection, start_at, end_at, q):
    # Empirically, Jira cloud won't return more than 100 issues at a
    # time even if you ask for more, but on-premise can do 1000
    #
    # This could be brittle, either with changes in Jira cloud, or with
    # different on-premise servers configured differently
    batch_size = 1000 if jira_connection.deploymentType == 'Server' else 100
    total_issues = end_at

    try:
        # pull batches and bulk load each into our database
        while start_at < min(end_at, total_issues):
            issues = _get_jira_issues_batch(jira_connection, start_at, batch_size)

            total_issues = issues.total
            issues_retrieved = len(issues)
            start_at += issues_retrieved
            if (issues_retrieved == 0):
                logger.warn(f'[Thread {i}] Jira issue downloaded might be stuck; asked for {batch_size} issues starting at {start_at}, '
                            f'ending at ending at {min(end_at, total_issues)}, got none back.  Trying again...')

            raw_issues = [i.raw for i in issues]

            # TODO: configurable way to scrub things out of raw_issues here before we write them out.
            
            q.put(raw_issues)

        # sentinel to mark that this thread finished
        q.put(None)

    except BaseException as e:
        logger.info(f'[Thread {i}] Jira issue downloader FAILED')
        q.put(e)


def _get_jira_issues_batch(jira_connection, start_at, max_results):
    get_changelog = True

    while (max_results > 0):
        search_kwargs = {
            'fields': '*all',
            'expand': 'renderedFields',
            'startAt': start_at,
            'maxResults': max_results,
        }
        if (get_changelog):
            search_kwargs['expand'] += ',changelog'

        try:
            return _expand_changelog(jira_connection.search_issues('created is not empty order by id asc',
                                                                  **search_kwargs), jira_connection)
        except JIRAError:
            logger.exception(f'JIRAError, reducing batch size')
            ## back off the max results requested to try and avoid the error
            max_results = int(max_results / 2)
            if max_results == 0:
                ## if we hit 0, then there's an issue we can't request with a changelog, fetch
                ## it without a change log and continue
                get_changelog = False
                max_results = 1


# Sometimes the results of issue search has an incomplete changelog.  Fill it in if so.
def _expand_changelog(jira_issues, jira_connection):
    for i in jira_issues:
        changelog = getattr(i, 'changelog', None)
        if changelog and changelog.total != changelog.maxResults:
            # expand the changelog
            start_at = changelog.maxResults
            batch_size = 100
            while (start_at < changelog.total):
                more_cls = jira_connection._get_json(f'issue/{i.id}/changelog', {'startAt':start_at, 'maxResults':batch_size})['values']
                changelog.histories.extend(dict2resource(i) for i in more_cls)
                i.raw['changelog']['histories'].extend(more_cls)
                start_at += len(more_cls)
    return jira_issues
