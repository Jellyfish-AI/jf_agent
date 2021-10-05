from jf_agent.git import NormalizedRepository

# Return branches for which we should pull commits, specified by customer in git config.
# The repo's default branch will always be included in the returned list.
def get_branches_for_normalized_repo(repo: NormalizedRepository, included_branches: dict):
    repo_branches = [repo.default_branch_name]
    additional_branches_for_repo = included_branches.get(repo.name)
    if additional_branches_for_repo:
        repo_branches.extend(additional_branches_for_repo)
    return set(repo_branches)