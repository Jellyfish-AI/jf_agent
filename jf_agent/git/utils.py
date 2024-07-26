import fnmatch
import logging
from typing import Any, List

logger = logging.getLogger(__name__)

'''

    Constants

'''
BBS_PROVIDER = 'bitbucket_server'
BBC_PROVIDER = 'bitbucket_cloud'
GH_PROVIDER = 'github'
GL_PROVIDER = 'gitlab'
PROVIDERS = (GL_PROVIDER, GH_PROVIDER, BBS_PROVIDER, BBC_PROVIDER)

# Must add a provider here to enable ingestion
# through jf_ingest for that provider
JF_INGEST_SUPPORTED_PROVIDERS = (GH_PROVIDER,)

# Return branches for which we should pull commits, specified by customer in git config.
# The repo's default branch will always be included in the returned list.
def get_branches_for_standardized_repo(repo: Any, included_branches: dict):
    branches_to_process = [repo.default_branch_name] if repo.default_branch_name else []
    additional_branches_for_repo = included_branches.get(repo.name)
    if additional_branches_for_repo:
        repo_branch_names = [b.name for b in repo.branches if b]
        branches_to_process.extend(
            get_matching_branches(additional_branches_for_repo, repo_branch_names)
        )
    return set(branches_to_process)


# Given a list of patterns, either literal branch names or names with wildcards (*) meant to match a set of branches in a repo,
# return the list of branches from repo_branches that match any of the branch name patterns.
# fnmatch is used over regex to support wildcards but avoid complicating the requirements on branch naming in a user's config.
def get_matching_branches(
    included_branch_patterns: List[str], repo_branch_names: List[str]
) -> List[str]:
    matching_branches = []
    for repo_branch_name in repo_branch_names:
        if any(fnmatch.fnmatch(repo_branch_name, pattern) for pattern in included_branch_patterns):
            matching_branches.append(repo_branch_name)
    return matching_branches


def log_and_print_request_error(e, action='making request', log_as_exception=False):
    from jf_ingest import logging_helper

    try:
        response_code = e.response_code
    except AttributeError:
        # if the request error is a retry error, we won't have the code
        response_code = ''

    error_name = type(e).__name__

    if log_as_exception:
        logging_helper.log_standard_error(
            logging.ERROR,
            msg_args=[error_name, response_code, action, e],
            error_code=3131,
            exc_info=True,
        )
    else:
        logging_helper.log_standard_error(
            logging.WARNING, msg_args=[error_name, response_code, action], error_code=3141
        )
