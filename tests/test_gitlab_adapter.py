import json
import unittest

from collections import namedtuple
from unittest import TestCase
from unittest.mock import MagicMock

from jf_agent.git import StandardizedShortRepository
from jf_agent.git.gitlab_adapter import GitLabAdapter

TEST_INPUT_FILE_PATH = 'tests/test_data/gitlab/'


class TestGitLabAdapter(TestCase):
    def setUp(self):
        self.mock_config = MagicMock()
        # Gitlab identifies repositories and groups by number,
        # so we use (lists of) numbers here. These map to the
        # `test/test-repo` repository in `test_repos.json`
        # and the `Test Group` group in `test_groups.json`
        included_repositories = [1]
        included_projects = [1]

        self.mock_config.git_include_repos = included_repositories
        self.mock_config.git_exclude_repos = None
        # Groups in Gitlab parlance == Projects in Jellyfish parlance
        self.mock_config.git_include_projects = included_projects
        self.mock_config.git_strip_text_content = False
        self.mock_config.git_redact_names_and_urls = False

        self.mock_client = MagicMock()

        self.outdir = "test"
        self.adapter = GitLabAdapter(self.mock_config, self.outdir, False, self.mock_client)

    def test_get_users(self):
        # Arrange
        test_users = _get_test_data('test_users.json')
        self.mock_client.list_group_members.return_value = test_users

        # Act
        resulting_users = self.adapter.get_users()

        # Assert
        self.assertEqual(
            len(resulting_users),
            len(test_users),
            f"Users should be a list of size {len(test_users)}",
        )

        for idx, test_user in enumerate(test_users):
            result_user = resulting_users[idx]
            self.assertEqual(
                result_user.id, test_user['id'], "resulting user id does not match input"
            )
            self.assertEqual(
                result_user.login,
                test_user['username'],
                "resulting user login does not match input",
            )
            self.assertEqual(
                result_user.name, test_user['name'], "resulting user name does not match input"
            )
            self.assertEqual(
                result_user.url, test_user['web_url'], "resulting user url does not match input"
            )
            self.assertIsNone(result_user.email)

    def test_get_projects(self):
        # Arrange
        test_groups = _get_test_data('test_groups.json')
        # Convert to named tuple to make fields accessible with dot notation
        api_group = namedtuple('api_group', test_groups[0].keys())(*test_groups[0].values())
        self.mock_client.get_group.return_value = api_group

        # Act
        resulting_projects = self.adapter.get_projects()

        # Assert
        self.assertEqual(
            len(resulting_projects),
            len(test_groups),
            f"Projects should be a list of size {len(test_groups)}",
        )

        resulting_project = resulting_projects[0]
        test_group = test_groups[0]
        self.assertEqual(
            resulting_project.id, test_group['id'], "resulting project id does not match input"
        )
        self.assertEqual(
            resulting_project.login,
            test_group['id'],
            "resulting project login does not match input",
        )
        self.assertEqual(
            resulting_project.name,
            test_group['name'],
            "resulting project name does not match input",
        )
        self.assertIsNone(resulting_project.url)

    def test_get_branches(self):
        # Arrange
        test_branches = _get_test_data('test_branches.json')
        mock_api_repo = MagicMock()

        # Convert to named tuple to make fields accessible with dot notation
        api_branch = namedtuple('api_branch', test_branches[0].keys())(*test_branches[0].values())
        self.mock_client.list_project_branches.return_value = [api_branch]

        # Act
        resulting_branches = self.adapter.get_branches(mock_api_repo)

        # Assert
        self.assertEqual(
            len(resulting_branches),
            len(test_branches),
            f"Branches should be a list of size {len(test_branches)}",
        )

        resulting_branch = resulting_branches[0]
        test_branch = test_branches[0]
        self.assertEqual(
            resulting_branch.name, test_branch['name'], "resulting branch id does not match input"
        )
        self.assertEqual(
            resulting_branch.sha,
            test_branch['commit']['id'],
            "resulting branch id does not match input",
        )

    def test_get_repos(self):
        # Arrange
        test_repos = _get_test_data('test_repos.json')
        test_groups = _get_test_data('test_groups.json')
        test_branches = _get_test_data('test_branches.json')

        # Convert to named tuples to make fields accessible with dot notation
        api_repo = namedtuple('api_repo', test_repos[0].keys())(*test_repos[0].values())
        api_group = namedtuple('api_group', test_groups[0].keys())(*test_groups[0].values())
        api_branch = namedtuple('api_branch', test_branches[0].keys())(*test_branches[0].values())
        self.mock_client.list_project_branches.return_value = [api_branch]

        self.mock_client.list_group_projects.return_value = [api_repo]
        self.mock_client.get_group.return_value = api_group
        self.mock_client.branches.list.return_value = [api_branch]

        # Act
        resulting_projects = self.adapter.get_projects()
        resulting_repos = self.adapter.get_repos(resulting_projects)

        # Assert
        self.assertEqual(
            len(resulting_repos),
            len(test_repos),
            f"Repos should be a list of size {len(test_repos)}",
        )

        resulting_repo = resulting_repos[0]
        test_repo = test_repos[0]
        self.assertEqual(
            resulting_repo.id, test_repo['id'], "resulting repo id does not match input"
        )
        self.assertEqual(
            resulting_repo.name, test_repo['name'], "resulting repo name does not match input"
        )
        self.assertEqual(
            resulting_repo.full_name,
            test_repo['name'],
            "resulting repo full_name does not match input",
        )
        self.assertEqual(
            resulting_repo.url, test_repo['web_url'], "resulting repo id does not match input"
        )
        self.assertEqual(
            resulting_repo.default_branch_name,
            test_repo['default_branch'],
            "resulting repo default branch does not match input",
        )

        self.assertEqual(
            len(resulting_repo.branches),
            len(test_branches),
            f"resulting branch list should a list of size {len(test_branches)}",
        )
        result_branch = resulting_repo.branches[0]
        test_branch = test_branches[0]
        self.assertEqual(
            result_branch.name, test_branch['name'], "resulting branch name does not match input"
        )
        self.assertEqual(
            result_branch.sha,
            test_branch['commit']['id'],
            "resulting branch sha does not match input",
        )

        test_group = test_groups[0]
        self.assertEqual(
            resulting_repo.project.id,
            test_group['id'],
            "resulting repo project id does not match input",
        )
        self.assertEqual(
            resulting_repo.project.name,
            test_group['name'],
            "resulting repo project name does not match input",
        )
        self.assertEqual(
            resulting_repo.project.login,
            test_group['id'],
            "resulting repo project login does not match input",
        )
        self.assertIsNone(resulting_repo.project.url)

    def test_get_branch_commits(self):
        # Arrange
        test_commits = _get_test_data('test_commits.json')
        mock_repo = MagicMock()
        mock_repos = [mock_repo]

        # Convert to named tuples to make fields accessible with dot notation
        api_commit = namedtuple('api_commits', test_commits[0].keys())(*test_commits[0].values())

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        self.mock_client.list_project_commits.return_value = [api_commit]
        mock_repo_url = "repo_url"
        mock_repo_default_branch = "default_branch_name"
        mock_repo.url = mock_repo_url
        mock_repo.name = "test_repo_name"
        mock_repo.default_branch_name = mock_repo_default_branch

        mock_short_repo = StandardizedShortRepository(
            id=1, name="test_repo_name", url="test_repo_url"
        )
        mock_repo.short.return_value = mock_short_repo

        # Act
        resulting_commits = list(
            self.adapter.get_commits_for_included_branches(
                mock_repos, {'test_repo_name': ['default_branch_name']}, test_git_instance_info
            )
        )

        # Assert
        self.assertEqual(
            len(resulting_commits),
            len(test_commits),
            f"Commits should be a list of size {len(test_commits)}",
        )

        result_commit = resulting_commits[0]
        test_commit = test_commits[0]
        self.assertEqual(
            result_commit.hash, test_commit['id'], "resulting commit hash does not match input"
        )
        self.assertEqual(
            result_commit.url,
            f"{mock_repo_url}/commit/{test_commit['id']}",
            "resulting commit url does not match input",
        )
        self.assertEqual(
            result_commit.commit_date,
            test_commit['committed_date'],
            "resulting commit date does not match input",
        )
        self.assertEqual(
            result_commit.author_date,
            test_commit['authored_date'],
            "resulting commit author date does not match input",
        )
        self.assertEqual(
            result_commit.message,
            test_commit['message'],
            "resulting commit message does not match input",
        )

        self.assertFalse(result_commit.is_merge)

        self.assertEqual(result_commit.repo.id, 1, "resulting commit repo id does not match input")
        self.assertEqual(
            result_commit.repo.name,
            "test_repo_name",
            "resulting commit repo name does not match input",
        )

        result_author = result_commit.author
        self.assertEqual(
            result_author.id,
            f"{test_commit['author_name']}<{test_commit['author_email']}>",
            "resulting commit author id does not match input",
        )
        self.assertEqual(
            result_author.login,
            test_commit['author_email'],
            "resulting commit author login does not match input",
        )
        self.assertEqual(
            result_author.name,
            test_commit['author_name'],
            "resulting commit author name does not match input",
        )
        self.assertEqual(
            result_author.email,
            test_commit['author_email'],
            "resulting commit author email does not match input",
        )

    def test_get_pull_requests(self):
        # Arrange
        test_commits = _get_test_data('test_commits.json')
        mock_repo = MagicMock()
        mock_repo.default_branch_name = 'default_branch_name'
        mock_repos = [mock_repo]

        # Convert to named tuples to make fields accessible with dot notation
        api_commit = namedtuple('api_commits', test_commits[0].keys())(*test_commits[0].values())

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        mock_pr = MagicMock()
        mock_pr.commit_list = [api_commit]
        mock_pr.updated_at = "2017-04-29T08:46:00Z"
        mock_pr.state = 'closed'

        mock_prs = MagicMock()
        mock_prs.total = 1
        mock_prs.__iter__.return_value = [mock_pr]

        self.mock_client.list_project_merge_requests.return_value = mock_prs
        self.mock_client.expand_merge_request_data.return_value = mock_pr

        # Act
        resulting_prs = list(self.adapter.get_pull_requests(mock_repos, test_git_instance_info))

        # Assert
        self.assertEqual(len(resulting_prs), 1, "Prs should be a list of size 1")

        resulting_pr = resulting_prs[0]
        self.assertEqual(resulting_pr.id, mock_pr.id, "resulting pr id does not match input")
        self.assertEqual(
            resulting_pr.created_at,
            mock_pr.created_at,
            "resulting pr created_at does not match input",
        )
        self.assertEqual(
            resulting_pr.updated_at,
            mock_pr.updated_at,
            "resulting pr updated_at does not match input",
        )
        self.assertEqual(
            resulting_pr.merge_date,
            mock_pr.merged_at,
            "resulting pr merge_date does not match input",
        )
        self.assertEqual(
            resulting_pr.closed_date,
            mock_pr.closed_at,
            "resulting pr closed_date does not match input",
        )
        self.assertTrue(resulting_pr.is_closed)
        self.assertFalse(resulting_pr.is_merged)
        self.assertEqual(resulting_pr.url, mock_pr.web_url, "resulting pr url does not match input")
        self.assertEqual(
            resulting_pr.base_branch,
            mock_pr.target_branch,
            "resulting pr base_branch does not match input",
        )
        self.assertEqual(
            resulting_pr.head_branch,
            mock_pr.source_branch,
            "resulting pr head_branch does not match input",
        )
        self.assertEqual(
            resulting_pr.title, mock_pr.title, "resulting pr title does not match input"
        )
        self.assertEqual(
            resulting_pr.body, mock_pr.description, "resulting pr body does not match input"
        )

        self.assertEqual(
            len(resulting_pr.commits),
            len(test_commits),
            f"Pr commits should be a list of size {len(test_commits)}",
        )
        pr_commit = resulting_pr.commits[0]
        test_commit = test_commits[0]
        self.assertEqual(
            pr_commit.hash, test_commit['id'], "resulting pr commit hash does not match input"
        )
        self.assertEqual(
            pr_commit.message,
            test_commit['message'],
            "resulting pr commit message does not match input",
        )
        self.assertFalse(pr_commit.is_merge)


def _get_test_data(file_name):
    with open(f'{TEST_INPUT_FILE_PATH}{file_name}', 'r') as f:
        return json.loads(f.read())


if __name__ == "__main__":
    unittest.main()
