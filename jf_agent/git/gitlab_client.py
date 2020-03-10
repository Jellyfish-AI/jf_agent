import gitlab
import logging
import requests

logger = logging.getLogger(__name__)


class GitLabClient:
    def __init__(self, server_url, private_token, per_page_override, session):
        kwargs = {'private_token': private_token, 'session': session}
        if per_page_override is not None:
            kwargs['per_page'] = per_page_override
        self.client = gitlab.Gitlab(server_url, **kwargs)

    @staticmethod
    def _get_diff_string(merge_request):
        changes = merge_request.changes()
        diffs = [change['diff'] for change in changes['changes']]
        return '\n'.join(diffs)

    def expand_merge_request_data(self, merge_request, target_project=None):
        """
        Modifies the merge_request object by obtaining and adding the following attributes:
            - 'approved_by'     [object]
            - 'approvers'       [object]
            - 'note_list'       [object]
            - 'commit_list'     [object]
            - 'target_project'  object
            - 'target_project'  object
            - 'diff'            string
        """
        try:
            merge_request.note_list = merge_request.notes.list(as_list=False)
        except (requests.exceptions.RetryError, gitlab.exceptions.GitlabHttpError) as e:
            logger.warning(
                f'Got {type(e)} ({e}) when fetching notes for merge_request {merge_request.id} -- '
                f'handling it as if it has no notes'
            )
            merge_request.note_list = []

        try:
            merge_request.diff = GitLabClient._get_diff_string(merge_request)
        except (requests.exceptions.RetryError, gitlab.exceptions.GitlabHttpError) as e:
            logger.warning(
                f'Got {type(e)} ({e}) when fetching changes for merge_request {merge_request.id} -- '
                f'handling it as if it has no diffs'
            )
            merge_request.diff = ''

        approvals = merge_request.approvals.get()
        merge_request.approved_by = approvals.approved_by
        merge_request.approvers = approvals.approvers

        # some usages already have the target project handy, in which case we can save a request
        # to the server, if not we'll go ahead and fetch it
        if target_project is None:
            target_project = self.get_project(merge_request.target_project_id)
        merge_request.target_project = target_project

        # the source project will be the same if the request is made from the same project
        # however, if the merge request is from a fork the source will be different and we'll
        # need to fetch its details
        if target_project.id != merge_request.source_project_id:
            merge_request.source_project = self.get_project(merge_request.source_project_id)
        else:
            merge_request.source_project = target_project

        # convert the 'commit_list' generator into a list of objects
        merge_request.commit_list = merge_request.commits()

        return merge_request

    def get_group(self, group_id):
        return self.client.groups.get(group_id)

    def get_project(self, project_id):
        return self.client.projects.get(project_id)

    def list_group_projects(self, group_id):
        group = self.get_group(group_id)
        return group.projects.list(as_list=False, include_subgroups=True)

    def list_group_members(self, group_id):
        group = self.get_group(group_id)
        return group.members.list(as_list=False)

    def list_project_branches(self, project_id):
        project = self.get_project(project_id)
        return project.branches.list(as_list=False)

    def list_project_merge_requests(self, project_id, state_filter=None):
        project = self.get_project(project_id)
        return project.mergerequests.list(
            state=state_filter, as_list=False, order_by='updated_at', sort='desc'
        )

    def list_project_commits(self, project_id, until_date):
        project = self.get_project(project_id)
        return project.commits.list(until=until_date, as_list=False)
