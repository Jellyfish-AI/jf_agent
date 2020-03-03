import gzip
import json
import jsonstreams
import pytz
from typing import List
from dataclasses import dataclass
from datetime import datetime, timedelta


def pull_since_date_for_repo(server_git_instance_info, org_login, repo_id, commits_or_prs):
    assert commits_or_prs in ('commits', 'prs')

    # Only a single instance is supported / sent from the server
    instance_info = list(server_git_instance_info.values())[0]

    instance_pull_from_dt = pytz.utc.localize(datetime.fromisoformat(instance_info['pull_from']))
    instance_info_this_repo = instance_info['repos_dict'].get(f'{org_login}-{repo_id}')

    if instance_info_this_repo:
        if commits_or_prs == 'commits':
            dt_str = instance_info_this_repo['commits_backpopulated_to']
        else:
            dt_str = instance_info_this_repo['prs_backpopulated_to']
        repo_backpop_to_dt = pytz.utc.localize(datetime.fromisoformat(dt_str)) if dt_str else None
        if not repo_backpop_to_dt or instance_pull_from_dt < repo_backpop_to_dt:
            # We need to backpopulate the repo
            return instance_pull_from_dt
        else:
            if commits_or_prs == 'commits':
                # We don't need to backpopulate the repo -- pull commits for last month
                return pytz.utc.localize(datetime.utcnow() - timedelta(days=31))
            else:
                # We don't need to backpopulate the repo -- only need to pull PRs that have been updated
                # more recently than PR with the latest update_date on the already-sent PRs
                return (
                    datetime.fromisoformat(instance_info_this_repo['latest_pr_update_date_pulled'])
                    if instance_info_this_repo['latest_pr_update_date_pulled']
                    else instance_pull_from_dt
                )
    else:
        # We need to backpopulate the repo
        return instance_pull_from_dt


def write_file(outdir, filename_prefix, compress, results):
    if compress:
        with gzip.open(f'{outdir}/{filename_prefix}.json.gz', 'wb') as outfile:
            outfile.write(json.dumps(results, indent=2, default=str).encode('utf-8'))
    else:
        with open(f'{outdir}/{filename_prefix}.json', 'w') as outfile:
            outfile.write(json.dumps(results, indent=2, default=str))


class StrDefaultEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        kwargs.update({'default': str})
        super().__init__(*args, **kwargs)


def download_and_write_streaming(
        outdir, filename_prefix, compress, generator_func, generator_func_args, item_id_dict_key
):
    if compress:
        outfile = gzip.open(f'{outdir}/{filename_prefix}.json.gz', 'wt')
    else:
        outfile = open(f'{outdir}/{filename_prefix}.json', 'w')

    item_ids = set()
    with jsonstreams.Stream(jsonstreams.Type.array, fd=outfile, encoder=StrDefaultEncoder) as s:
        for item in generator_func(*generator_func_args):
            if isinstance(item, list):
                for i in item:
                    s.write(i)
                    item_ids.add(i[item_id_dict_key])
            else:
                s.write(item)
                item_ids.add(item[item_id_dict_key])

    outfile.close()
    return item_ids


@dataclass
class NormalizedUser:
    id: str
    name: str
    login: str
    email: str = None


@dataclass
class NormalizedBranch:
    name: str
    sha: str


@dataclass
class NormalizedProject:
    id: str
    name: str
    login: str
    url: str


@dataclass
class NormalizedRepository:
    id: int
    name: str
    full_name: str
    url: str
    is_fork: bool
    default_branch_name: str
    project: NormalizedProject
    branches: List[NormalizedBranch]


@dataclass
class NormalizedCommit:
    hash: str
    url: str
    message: str
    commit_date: str
    author_date: str
    author: NormalizedUser
    repo: NormalizedRepository
    is_merge: bool


@dataclass
class NormalizedComment:
    user: NormalizedUser
    body: str
    created_at: str


@dataclass
class NormalizedReview:
    foreign_id: any
    review_state: str
    user: NormalizedUser


@dataclass
class NormalizedPullRequest:
    id = any
    additions = int
    deletions = int
    changed_files: int
    is_closed: bool
    is_merged: bool
    created_at: str
    updated_at: str
    merge_date: str
    closed_date: str
    title: str
    body: str
    url: str
    author: NormalizedUser
    merged_by: NormalizedUser
    base_branch: NormalizedBranch
    head_branch: NormalizedBranch
    comments: List[NormalizedComment]
    commits: List[NormalizedCommit]
    approvals: List[NormalizedReview]
    base_repo: NormalizedRepository
    head_repo: NormalizedRepository


@dataclass
class NormalizedPullRequestComment:
    user: NormalizedUser
    body: str
    created_at: str


@dataclass
class NormalizedPullRequestReview:
    user: NormalizedUser
    foreign_id: int
    review_state: str
