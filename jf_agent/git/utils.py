import re
from typing import List
from jf_agent.git import NormalizedRepository

# Return branches for which we should pull commits, specified by customer in git config.
# The repo's default branch will always be included in the returned list.
def get_branches_for_normalized_repo(repo: NormalizedRepository, included_branches: dict):
    branches_to_process = [repo.default_branch_name]
    additional_branches_for_repo = included_branches.get(repo.name)
    if additional_branches_for_repo:
        repo_branch_names = [b.name for b in repo.branches]
        branches_to_process.extend(get_matching_branches(additional_branches_for_repo, repo_branch_names))
    return set(branches_to_process)

# Given a list of patterns, either literal branch names or regexes meant to match a set of branches in a repo, 
# return the list of branches from repo_branches match any of the patterns.
def get_matching_branches(included_branch_patterns: List[str], repo_branch_names: List[str]) -> List[str]:
    matching_branches = []
    for repo_branch_name in repo_branch_names:
        if any(re.compile(pattern).fullmatch(repo_branch_name) for pattern in included_branch_patterns):
            matching_branches.append(repo_branch_name)
    return matching_branches