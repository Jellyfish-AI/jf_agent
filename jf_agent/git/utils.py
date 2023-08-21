import fnmatch
import logging
from typing import Any, List

logger = logging.getLogger(__name__)

# Return branches for which we should pull commits, specified by customer in git config.
# The repo's default branch will always be included in the returned list.
def get_branches_for_normalized_repo(repo: Any, included_branches: dict):
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
    from jf_agent import agent_logging

    try:
        response_code = e.response_code
    except AttributeError:
        # if the request error is a retry error, we won't have the code
        response_code = ''

    error_name = type(e).__name__

    if log_as_exception:
        agent_logging.log_and_print_error_or_warning(
            logger,
            logging.ERROR,
            msg_args=[error_name, response_code, action, e],
            error_code=3131,
            exc_info=True,
        )
    else:
        agent_logging.log_and_print_error_or_warning(
            logger, logging.WARNING, msg_args=[error_name, response_code, action], error_code=3141
        )
