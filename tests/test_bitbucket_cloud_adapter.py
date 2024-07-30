import json
import unittest

from dateutil import parser
from unittest import TestCase
from unittest.mock import MagicMock

from jf_agent.git import StandardizedShortRepository
from jf_agent.git.bitbucket_cloud_adapter import BitbucketCloudAdapter

TEST_INPUT_FILE_PATH = 'tests/test_data/bitbucket_cloud/'


class TestBitbucketCloudAdapter(TestCase):
    def setUp(self):
        self.mock_config = MagicMock()
        self.mock_config.git_include_repos = None
        self.mock_config.git_exclude_repos = None
        self.mock_config.git_strip_text_content = False
        self.mock_config.git_redact_names_and_urls = False

        self.mock_client = MagicMock()

        self.outdir = "test"
        self.adapter = BitbucketCloudAdapter(self.mock_config, self.outdir, False, self.mock_client)

    def test_get_users(self):
        # Act
        users = self.adapter.get_users()

        # Assert
        self.assertEqual(users, [], "Should be an empty list")

    def test_get_users(self):
        # Arrange
        input_projects = ['test_project_1', 'test_project_2']
        self.mock_config.git_include_projects = input_projects

        # Act
        resulting_projects = self.adapter.get_projects()

        # Assert
        self.assertEqual(
            len(resulting_projects),
            len(input_projects),
            f"Should be project list of size {len(input_projects)}",
        )

        for idx, resulting_project in enumerate(resulting_projects):
            self.assertEqual(
                resulting_project.id,
                input_projects[idx],
                "Project id should be the same as the input project name",
            )
            self.assertEqual(
                resulting_project.name,
                input_projects[idx],
                "Project name should be the same as the input project name",
            )
            self.assertEqual(
                resulting_project.login,
                input_projects[idx],
                "Project login should be the same as the input project name",
            )
            self.assertIsNone(resulting_project.url)

    def test_get_repos(self):
        # Arrange
        test_repos = _get_test_data('test_repos.json')
        test_branches = _get_test_data('test_branches.json')

        mock_standardized_project = MagicMock()
        mock_standardized_projects = [mock_standardized_project]

        self.mock_client.get_all_repos.return_value = test_repos
        self.mock_client.get_branches.return_value = test_branches

        # Act
        resulting_repos = self.adapter.get_repos(mock_standardized_projects)

        # Assert
        self.assertEqual(
            len(resulting_repos),
            len(test_repos),
            f"Resulting repos should be a list of size {len(test_repos)}",
        )
        resulting_repo = resulting_repos[0]
        input_repo = test_repos[0]
        self.assertEqual(
            resulting_repo.id, input_repo['uuid'], "Resulting repo id does not match input"
        )
        self.assertEqual(
            resulting_repo.name, input_repo['name'], "Resulting repo name does not match input"
        )
        self.assertEqual(
            resulting_repo.full_name,
            input_repo['full_name'],
            "Resulting repo full_name does not match input",
        )
        self.assertEqual(
            resulting_repo.url,
            input_repo['links']['self']['href'],
            "Resulting repo url does not match input",
        )
        self.assertFalse(resulting_repo.is_fork)
        self.assertEqual(
            resulting_repo.default_branch_name,
            input_repo['mainbranch']['name'],
            "Resulting repo default_branch_name does not match input",
        )
        self.assertEqual(
            resulting_repo.project,
            mock_standardized_project,
            "Resulting repo project does not match input",
        )

        self.assertEqual(
            len(resulting_repo.branches),
            len(test_branches),
            f"Resulting repo should have {len(test_branches)} branch",
        )
        resulting_branch = resulting_repo.branches[0]
        input_branch = test_branches[0]
        self.assertEqual(
            resulting_branch.name,
            input_branch['name'],
            "Resulting branch name does not match input",
        )
        self.assertEqual(
            resulting_branch.sha,
            input_branch['target']['hash'],
            "Resulting branch name does not match input",
        )

    def test_get_branch_commits(self):
        # Arrange
        test_commits = _get_test_data('test_commits.json')

        mock_standardized_repo = MagicMock()
        mock_standardized_repo.default_branch_name = 'default_branch_name'
        test_short_repo = StandardizedShortRepository(
            id='test_id', name='test_name', url='test_url'
        )
        mock_standardized_repo.short.return_value = test_short_repo
        mock_standardized_repos = [mock_standardized_repo]

        self.mock_client.get_commits.return_value = test_commits

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act
        resulting_commits = list(
            self.adapter.get_commits_for_included_branches(
                mock_standardized_repos, {'test_repo': ['test_branch_name']}, test_git_instance_info
            )
        )

        # Assert
        self.assertEqual(
            len(resulting_commits),
            len(test_commits),
            f"Resulting commits should be a list of size {len(test_commits)}",
        )
        resulting_commit = resulting_commits[0]
        input_commit = test_commits[0]
        self.assertEqual(
            resulting_commit.hash,
            input_commit['hash'],
            "Resulting commit hash does not match input",
        )
        self.assertEqual(
            resulting_commit.author.name,
            input_commit['author']['user']['display_name'],
            "Resulting commit author name does not match input",
        )
        self.assertEqual(
            resulting_commit.url,
            input_commit['links']['html']['href'],
            "Resulting commit url does not match input",
        )
        self.assertEqual(
            resulting_commit.commit_date,
            parser.parse(input_commit['date']),
            "Resulting commit date does not match input",
        )
        self.assertEqual(
            resulting_commit.message,
            input_commit['message'],
            "Resulting commit message does not match input",
        )
        self.assertEqual(
            resulting_commit.repo.id,
            test_short_repo.id,
            "Resulting commit's repo id does not match input",
        )
        self.assertEqual(
            resulting_commit.repo.name,
            test_short_repo.name,
            "Resulting commit's repo name does not match input",
        )
        self.assertEqual(
            resulting_commit.repo.url,
            test_short_repo.url,
            "Resulting commit's repo url does not match input",
        )
        self.assertFalse(resulting_commit.is_merge)
        self.assertIsNone(resulting_commit.author_date)

    def test_get_prs(self):
        # Arrange
        test_prs = _get_test_data('test_prs.json')
        test_commits = _get_test_data('test_commits.json')

        mock_standardized_repo = MagicMock()
        mock_standardized_repo.default_branch_name = 'default_branch_name'
        mock_standardized_repos = [mock_standardized_repo]

        self.mock_client.get_pullrequests.return_value = test_prs
        self.mock_client.pr_diff.return_value = ""
        self.mock_client.pr_comments.return_value = []
        self.mock_client.pr_activity.return_value = []
        self.mock_client.pr_commits.return_value = test_commits
        self.mock_client.get_commit.return_value = test_commits[0]

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act
        resulting_prs = list(
            self.adapter.get_pull_requests(mock_standardized_repos, test_git_instance_info)
        )

        # Assert
        self.assertEqual(
            len(resulting_prs),
            len(test_prs),
            f"Resulting prs should be a list of size {len(test_prs)}",
        )
        resulting_pr = resulting_prs[0]
        input_pr = test_prs[0]
        self.assertEqual(resulting_pr.id, input_pr['id'], "Resulting pr id does not match input")
        self.assertEqual(
            resulting_pr.title, input_pr['title'], "Resulting pr title does not match input"
        )
        self.assertEqual(
            resulting_pr.body,
            input_pr['description'],
            "Resulting pr description does not match input",
        )
        self.assertEqual(
            resulting_pr.url,
            input_pr['links']['html']['href'],
            "Resulting pr url does not match input",
        )
        self.assertEqual(
            resulting_pr.base_branch,
            input_pr['destination']['branch']['name'],
            "Resulting pr base branch does not match input",
        )
        self.assertEqual(
            resulting_pr.head_branch,
            input_pr['source']['branch']['name'],
            "Resulting pr head branch does not match input",
        )
        self.assertEqual(
            resulting_pr.author.name,
            input_pr['author']['display_name'],
            "Resulting pr author name does not match input",
        )

        self.assertEqual(
            len(resulting_pr.commits),
            len(test_commits),
            f"Resulting pr should have {len(test_commits)} commit",
        )
        self.assertEqual(
            resulting_pr.commits[0].hash,
            test_commits[0]['hash'],
            "Resulting pr should have hash matching input commit",
        )
        self.assertFalse(resulting_pr.is_closed)
        self.assertFalse(resulting_pr.is_merged)


def _get_test_data(file_name):
    with open(f'{TEST_INPUT_FILE_PATH}{file_name}', 'r') as f:
        return json.loads(f.read())


if __name__ == "__main__":
    unittest.main()
