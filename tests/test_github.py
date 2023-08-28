import json
import unittest

from unittest import TestCase
from unittest.mock import MagicMock

from jf_agent.git import github

TEST_INPUT_FILE_PATH = f'tests/test_data/github/'


class TestGithub(TestCase):
    def test_get_users(self):
        # Arrange
        test_users = _get_test_data('test_users.json')

        mock_client = MagicMock()
        mock_client.get_all_users.return_value = test_users

        # Act
        result_users = github.get_users(mock_client, ['test_org'])

        # Assert
        self.assertEqual(len(result_users), 2, "Should be user list of size 2")
        for idx, resulting_user in enumerate(result_users):
            input_user = test_users[idx]
            self.assertEqual(resulting_user.id, input_user.get('id'), "user ids should match")
            self.assertEqual(
                resulting_user.login, input_user.get('login'), "user logins should match"
            )
            self.assertEqual(resulting_user.name, input_user.get('name'), "user names should match")
            self.assertEqual(
                resulting_user.email, input_user.get('email'), "user emails should match"
            )

    def test_get_projects(self):
        # Arrange
        test_projects = _get_test_data('test_projects.json')
        mock_client = MagicMock()
        mock_client.get_organization_by_name.return_value = test_projects[0]

        # Act
        result_projects = github.get_projects(mock_client, ['test_org'], False)

        # Assert
        self.assertEqual(len(result_projects), 1, "project size should be 1")

        # get_projects returns a list of (api_object, standardized_project). Use the standardized version for verification.
        result_project = result_projects[0]
        input_project = test_projects[0]
        self.assertEqual(
            result_project.id, input_project['id'], "resulting project id does not match input"
        )
        self.assertEqual(
            result_project.login,
            input_project['login'],
            "resulting project login does not match input",
        )
        self.assertEqual(
            result_project.name,
            input_project['name'],
            "resulting project name does not match input",
        )
        self.assertEqual(
            result_project.url,
            input_project['html_url'],
            "resulting project url does not match input",
        )

    def test_get_repos(self):
        # Arrange
        test_repos = _get_test_data('test_repos.json')
        test_branches = _get_test_data('test_branches.json')
        test_projects = _get_test_data('test_projects.json')

        mock_client = MagicMock()
        mock_client.get_all_repos.return_value = test_repos
        mock_client.get_branches.return_value = test_branches
        mock_client.get_json.return_value = test_projects[0]

        # Act
        result_repos = github.get_repos(mock_client, ['test_org'], [], [], False)

        # Assert
        self.assertEqual(len(result_repos), 1, "repo size should be 1")

        # get_repos returns a list of (api_object, standardized_project). Use the standardized version for verification.
        result_repo = result_repos[0][1]
        test_repo = test_repos[0]
        self.assertEqual(result_repo.id, test_repo['id'], "resulting repo id does not match input")
        self.assertEqual(
            result_repo.name, test_repo['name'], "resulting repo name does not match input"
        )
        self.assertEqual(
            result_repo.full_name,
            test_repo['full_name'],
            "resulting repo full_name does not match input",
        )
        self.assertEqual(
            result_repo.url, test_repo['html_url'], "resulting repo url does not match input"
        )
        self.assertEqual(
            result_repo.default_branch_name,
            test_repo['default_branch'],
            "resulting repo has unexpected default branch",
        )
        self.assertFalse(result_repo.is_fork)

        # Assert expected branches exist
        self.assertEqual(
            len(result_repo.branches),
            len(test_branches),
            f"resulting repo should have {len(test_branches)} branches",
        )
        for idx, test_branch in enumerate(test_branches):
            result_branch = result_repo.branches[idx]
            self.assertEqual(
                result_branch.name,
                test_branch['name'],
                "resulting branch name does not match input",
            )
            self.assertEqual(
                result_branch.sha,
                test_branch['commit']['sha'],
                "resulting branch sha does not match input",
            )

    def test_get_branch_commits(self):
        # Arrange
        test_repos = _get_test_data('test_repos.json')
        test_commits = _get_test_data('test_commits.json')

        mock_client = MagicMock()
        mock_client.get_commits.return_value = test_commits

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act
        result_commits = list(
            github.get_commits_for_included_branches(
                mock_client,
                test_repos,
                {'repo_name': ['master']},
                False,
                test_git_instance_info,
                False,
            )
        )

        # Assert
        self.assertEqual(len(result_commits), 1, "commit size should be 1")
        for idx, test_commit in enumerate(test_commits):
            result_commit = result_commits[idx]
            self.assertEqual(
                result_commit.hash, test_commit['sha'], "resulting commit hash does not match input"
            )
            self.assertEqual(
                result_commit.author.id,
                test_commit['author']['id'],
                "resulting author does not match input",
            )
            self.assertEqual(
                result_commit.url, test_commit['html_url'], "resulting url does not match input"
            )
            self.assertEqual(
                result_commit.message,
                test_commit['commit']['message'],
                "resulting message does not match input",
            )
            self.assertFalse(result_commit.is_merge)

            expected_repo = test_repos[0]
            self.assertEqual(
                result_commit.repo.id, expected_repo['id'], "resulting repo id does not match input"
            )
            self.assertEqual(
                result_commit.repo.name,
                expected_repo['name'],
                "resulting repo name does not match input",
            )
            self.assertEqual(
                result_commit.repo.url,
                expected_repo['html_url'],
                "resulting repo links do not match input",
            )

    def test_get_pull_requests(self):
        # Arrange
        test_users = _get_test_data('test_users.json')
        test_repos = _get_test_data('test_repos.json')
        test_prs = _get_test_data('test_prs.json')
        test_commits = _get_test_data('test_commits.json')

        mock_client = MagicMock()
        mock_client.get_pullrequests.return_value = test_prs
        mock_client.get_pr_commits.return_value = test_commits
        mock_client.get_json.return_value = test_users[0]

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act
        result_prs = list(
            github.get_pull_requests(mock_client, test_repos, False, test_git_instance_info, False)
        )

        # Assert
        self.assertEqual(len(result_prs), 1, "Pr size should be 1")
        result_pr = result_prs[0]
        test_pr = test_prs[0]
        self.assertEqual(result_pr.id, test_pr['number'], "resulting pr id does not match input")
        self.assertEqual(
            result_pr.additions, test_pr['additions'], "resulting pr additions does not match input"
        )
        self.assertEqual(
            result_pr.deletions, test_pr['deletions'], "resulting pr deletions does not match input"
        )
        self.assertEqual(
            result_pr.changed_files,
            test_pr['changed_files'],
            "resulting pr changed_files does not match input",
        )
        self.assertEqual(
            result_pr.created_at,
            test_pr['created_at'],
            "resulting pr created_at does not match input",
        )
        self.assertEqual(
            result_pr.updated_at,
            test_pr['updated_at'],
            "resulting pr updated_at does not match input",
        )
        self.assertEqual(
            result_pr.merge_date,
            test_pr['merged_at'],
            "resulting pr merge_date does not match input",
        )
        self.assertEqual(
            result_pr.closed_date,
            test_pr['closed_at'],
            "resulting pr closed_date does not match input",
        )
        self.assertEqual(
            result_pr.title, test_pr['title'], "resulting pr title does not match input"
        )
        self.assertEqual(result_pr.body, test_pr['body'], "resulting pr body does not match input")
        self.assertEqual(
            result_pr.url, test_pr['html_url'], "resulting pr url does not match input"
        )
        self.assertEqual(
            result_pr.base_branch,
            test_pr['base']['ref'],
            "resulting pr base_branch does not match input",
        )
        self.assertEqual(
            result_pr.head_branch,
            test_pr['head']['ref'],
            "resulting pr head_branch does not match input",
        )
        self.assertEqual(
            result_pr.author.id, test_users[0]['id'], "resulting pr author id does not match input"
        )
        self.assertEqual(
            len(result_pr.commits),
            len(test_commits),
            "resulting pr commits length does not match input",
        )
        self.assertEqual(
            result_pr.commits[0].hash,
            test_commits[0]['sha'],
            "resulting pr commit hash does not match input",
        )
        self.assertEqual(
            result_pr.base_repo.id,
            test_pr['base']['repo']['id'],
            "resulting pr base repo id does not match input",
        )
        self.assertEqual(
            result_pr.head_repo.id,
            test_pr['head']['repo']['id'],
            "resulting pr head repo id does not match input",
        )

        self.assertFalse(result_pr.is_closed)
        self.assertFalse(result_pr.is_merged)

        self.assertIsNone(result_pr.merge_commit)


def _get_test_data(file_name):
    with open(f'{TEST_INPUT_FILE_PATH}{file_name}', 'r') as f:
        return json.loads(f.read())


if __name__ == "__main__":
    unittest.main()
