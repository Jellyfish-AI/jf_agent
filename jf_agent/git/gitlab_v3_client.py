from datetime import datetime, timezone
import logging

import gitlab3
import requests

from jf_agent import agent_logging

logger = logging.getLogger(__name__)


class MissingSourceProjectException(Exception):
    pass


def log_and_print_request_error(e, action='making request', log_as_exception=False):
    try:
        response_code = e.response_code
    except AttributeError:
        # if the request error is a retry error, we won't have the code
        response_code = ''

    error_name = type(e).__name__

    if log_as_exception:
        agent_logging.log_and_print_error_or_warning(
            logger, logging.ERROR, msg_args=[error_name, response_code, action, e], error_code=3131,
        )
    else:
        agent_logging.log_and_print_error_or_warning(
            logger, logging.WARNING, msg_args=[error_name, response_code, action], error_code=3141
        )


class GitLabClient_v3:
    """
    __init__(self, server_url, token=None, convert_dates=True, ssl_verify=None, ssl_cert=None)

    Initialize a GitLab connection and optionally supply auth token.
    convert_dates can be set to False to disable automatic conversion of date strings to datetime objects.
    ssl_verify and ssl_cert are passed to python-requests as the verify and cert arguments, respectively.
    """

    def __init__(self, server_url, token, convert_dates=True, ssl_verify=None, ssl_cert=None):
        kwargs = {'token': token, 'convert_dates': convert_dates}
        if ssl_cert is not None:
            kwargs['ssl_cert'] = ssl_cert
        if not ssl_verify:
            kwargs['ssl_verify'] = False
        self.server_url = server_url
        self.agent_args = kwargs
        self.client = gitlab3.GitLab(server_url, **kwargs)
        self.api_version = '3'

    @staticmethod
    def _get_diff_string(diffs):
        if diffs:
            diffs = [diff_str for sublist in diffs for diff_str in sublist]
            return '\n'.join(diffs)
        else:
            return None

    def expand_merge_request_data(self, merge_request):
        """
        Modifies the merge_request object by obtaining and adding the following attributes:
            - 'approved_by'     [object]
            - 'note_list'       [object]
            - 'commit_list'     [object]
            - 'target_project'  object
            - 'target_project'  object
            - 'diff'            string
        """
        target_project = self.get_project(merge_request.target_project_id)
        merge_request.target_project = target_project
        # the source project will be the same if the request is made from the same project
        # however, if the merge request is from a fork the source will be different and we'll
        # need to fetch its details
        if target_project.id != merge_request.source_project_id:
            try:
                merge_request.source_project = self.get_project(merge_request.source_project_id)
            except gitlab3.exceptions.GitLabException as e:
                if e.response_code == 404:
                    raise MissingSourceProjectException()
                raise
        else:
            merge_request.source_project = target_project

        # get notes data
        try:
            merge_request.note_list = merge_request.notes()
        except (requests.exceptions.RetryError, gitlab3.exceptions.GitLabException) as e:
            log_and_print_request_error(
                e,
                f'fetching notes for merge_request {merge_request.id} -- '
                f'handling it as if it has no notes',
            )
            merge_request.note = []

        # get commit.diff() data
        try:
            merge_request.diff = GitLabClient_v3._get_diff_string(merge_request.diff)
        except (requests.exceptions.RetryError, gitlab3.exceptions.GitLabException) as e:
            log_and_print_request_error(
                e,
                f'fetching changes for merge_request {merge_request.id} -- '
                f'handling it as if it has no diffs',
            )
            merge_request.diff = ''

        # convert the 'commit_list' generator into a list of objects
        commit_evnts = target_project.find_event(action_name='pushed to', find_all=True)
        merge_request.commit_list = self.get_mergerequest_commits(
            merge_request, commit_evnts, target_project
        )
        return merge_request

    def get_group(self, group_id):
        return self.client.get_group(group_id)

    def get_branch(self, project, branch_name):
        return project.find_branch(name=branch_name)

    def get_project(self, project_id):
        return self.client.find_project(id=project_id)

    def list_group_projects(self, group_id=None):
        projects = self.client.projects()
        return projects

    def list_group_members(self, group_id):
        group = self.get_group(group_id)
        return group.members()

    def list_project_branches(self, project_id):
        project = self.get_project(project_id)
        return project.branches()

    def list_project_merge_requests(self, project_id, state_filter=None):
        project = self.get_project(project_id)
        mergerequests = project.merge_requests()
        commit_evnts = project.find_event(action_name='pushed to', find_all=True)
        if len(mergerequests) > 0:
            if state_filter:
                mergerequests = [entry for entry in mergerequests if entry.state in state_filter]
            mergerequests.sort(key=lambda x: x.created_at, reverse=True)
            mergerequests = self.add_v4_attrs('mergerequest', commit_evnts, mergerequests, project)
            return mergerequests
        return mergerequests

    def list_project_commits(self, project_id, since_date):
        project = self.get_project(project_id)
        mrg_evnts = project.find_event(action_name='pushed to', find_all=True)
        mrg_shas = []
        for event in mrg_evnts:
            mrg_shas.append(event.data['before'])
            mrg_shas.append(event.data['after'])
        commits = self.add_v4_attrs('commit', mrg_shas, list(project.commits()), project)
        if len(commits) > 0:
            if since_date:
                commits = [commit for commit in commits if commit.created_at > since_date]
                commits.sort(key=lambda x: x.created_at, reverse=True)
            return commits
        return []

    def get_project_commit(self, project_id, sha):
        project = self.get_project(project_id)
        mrg_commit = project.find_commit(id=sha)  # only searches master branch by default
        mrg_evnts = project.find_event(action_name='pushed to', find_all=True)
        mrg_shas = []
        for event in mrg_evnts:
            mrg_shas.append(event.data['before'])
            mrg_shas.append(event.data['after'])
        if mrg_commit:
            try:
                commit = self.add_v4_attrs('commit', mrg_shas, [mrg_commit], project)
                return commit[0]
            except gitlab3.exceptions.GitLabException:
                return None
        return None

    def get_branch_commit(self, branch_name, sha):
        branch = self.get_branch(branch_name)
        branch_commit_id = branch.commit['parent_ids'][0]
        if sha == branch_commit_id:
            project = branch._parent
            parent_commit = self.get_project_commit(project.id, branch_commit_id)
            merge_commit = project.Commit(parent=parent_commit)
            commit_evnts = project.find_event(action_name='pushed to', find_all=True)
            mrg_shas = []
            for event in commit_evnts:
                mrg_shas.append(event.data['before'])
                mrg_shas.append(event.data['after'])
            commit = self.add_v4_attrs('commit', mrg_shas, [merge_commit], project)
            return commit[0]
        else:
            return None

    def get_mergerequest_commits(self, mergerequest, commit_evnts, project=None):
        if project == None:
            project = self.client.find_project(id=mergerequest.project_id)
        mrg_shas = []
        for event in commit_evnts:
            mrg_shas.append(event.data['before'])
            mrg_shas.append(event.data['after'])
        commits = mergerequest.get_commits()
        commits.sort(key=lambda x: x['created_at'], reverse=False)
        payload = []
        if commits:
            # search project for merged PR commits
            for i, commit_dict in enumerate(commits):
                commit = self.get_project_commit(project.id, commit_dict['id'])
                if commit:
                    payload.append(commit)

            # manually create commit object if api returns no PR commits have been merged
            if payload:
                return payload
            else:
                # getting most recent branch HEAD that was merged into master
                all_requests = project.merge_requests()
                branch_mrgs = [
                    m
                    for m in all_requests
                    if m.state == 'merged' and m.source_branch == mergerequest.source_branch
                ]
                if branch_mrgs:
                    branch_mrgs.sort(key=lambda x: x.created_at, reverse=True)
                    parent_commit = project.find_commit(id=branch_mrgs[0].sha)
                else:
                    # if no such HEAD exists, get the most recent HEAD from the branch where the merge request originated
                    all_requests.sort(key=lambda x: x.created_at, reverse=True)
                    parent_commit = project.find_commit(id=all_requests[0].sha)
                # creating v4-compatible commit objects from v3-provided commit dicts
                for i, commit_dict in enumerate(commits):
                    merge_commit = project.Commit(parent=parent_commit)
                    # adding base v3 attrs here...
                    merge_commit.__setattr__('author_email', commit_dict['author_email'])
                    merge_commit.__setattr__('author_name', commit_dict['author_name'])
                    merge_commit.__setattr__('committer_email', commit_dict['author_email'])
                    merge_commit.__setattr__('committer_name', commit_dict['author_name'])
                    merge_commit.__setattr__(
                        'created_at',
                        datetime.strptime(commit_dict['created_at'], "%Y-%m-%dT%H:%M:%S.%f%z").replace(tzinfo=timezone.utc),
                    )
                    merge_commit.__setattr__('id', commit_dict['id'])
                    merge_commit.__setattr__('message', commit_dict['message'])
                    merge_commit.__setattr__('short_id', commit_dict['short_id'])
                    merge_commit.__setattr__('title', commit_dict['title'])
                    merge_commit.__setattr__('agent_made', True)

                    # adding base v4 attrs here...
                    commit = self.add_v4_attrs('commit', commit_evnts, [merge_commit], project)
                    parent_commit = merge_commit
                    payload.extend(commit)
                return payload
        return payload

    def add_v4_attrs(self, argtype, argevnts, dataset, project):
        if argtype == 'mergerequest':
            for i, entry in enumerate(dataset):
                mrg_approval_evnt = project.find_event(
                    action_name='accepted',
                    target_type='MergeRequest',
                    target_title=entry.title,
                )
                if mrg_approval_evnt:
                    approved_by = [{'user': mrg_approval_evnt.author}]
                    mrg_date = mrg_approval_evnt.created_at.astimezone(timezone.utc).isoformat()
                    merged_by = approved_by[0]['user'] if approved_by else ''
                else:
                    approved_by, mrg_date, merged_by = '', '', ''
                commits = self.get_mergerequest_commits(entry, argevnts, project)
                entry.__setattr__('merge_date', mrg_date)
                entry.__setattr__('approved_by', approved_by)
                entry.__setattr__('merged_by', merged_by)
                entry.__setattr__(
                    'created_at', entry.created_at.astimezone(timezone.utc).isoformat()
                )
                entry.__setattr__(
                    'updated_at', entry.updated_at.astimezone(timezone.utc).isoformat()
                )
                entry.__setattr__('updated_at_dt', entry.updated_at)
                entry.__setattr__('closed_at', mrg_date)
                entry.__setattr__('base_branch', entry.target_branch)
                entry.__setattr__('head_branch', entry.source_branch)
                if commits:
                    diffs = []
                    for commit in commits:
                        commit_keys = list(dir(commit))
                        if 'agent_made' in commit_keys:
                            continue
                        else:
                            diffs.extend(commit.diff())
                    if len(diffs) > 0:
                        diffs = [obj['diff'].splitlines() for obj in diffs]
                    entry.__setattr__('diff', diffs)
                else:
                    entry.__setattr__('diff', '')
                dataset[i] = entry
        elif argtype == 'commit':
            mrg_shas = argevnts
            for i, entry in enumerate(dataset):
                entry.__setattr__(
                    'authored_date', entry.created_at.astimezone(timezone.utc).isoformat()
                )
                entry.__setattr__(
                    'committed_date', entry.created_at.astimezone(timezone.utc).isoformat()
                )
                if entry.id in mrg_shas:
                    entry.__setattr__('parent_ids', [1, 2, 3])
                else:
                    entry.__setattr__(
                        'parent_ids', []
                    )  # TODO: setting empty list potentially problematic
                dataset[i] = entry
        return dataset
