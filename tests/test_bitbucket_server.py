import json
import unittest

from unittest import TestCase
from unittest.mock import MagicMock

from jf_agent.git import bitbucket_server

TEST_INPUT_FILE_PATH = f'tests/test_data/bitbucket_server/'


class TestBitbucketServer(TestCase):
    def test_get_users(self):
        # Arrange
        test_users = _get_test_data('test_users.json')
        mock_client = MagicMock()
        mock_client.admin.users = test_users

        # Act
        result_users = bitbucket_server.get_users(mock_client)

        # Assert
        self.assertEqual(
            len(result_users), len(test_users), f"Should be user list of size {test_users}"
        )
        for idx, resulting_user in enumerate(result_users):
            input_user = test_users[idx]
            self.assertEqual(
                resulting_user.get('id'), input_user.get('id'), "user ids should match"
            )
            self.assertEqual(
                resulting_user.get('login'), input_user.get('name'), "user logins should match"
            )
            self.assertEqual(
                resulting_user.get('name'), input_user.get('displayName'), "user names should match"
            )
            self.assertEqual(
                resulting_user.get('email'),
                input_user.get('emailAddress'),
                "user emails should match",
            )

    def test_get_projects(self):
        # Arrange
        test_projects = _get_test_data('test_projects.json')
        mock_client = MagicMock()
        mock_client.projects.list.return_value = test_projects

        # Act
        result_projects = bitbucket_server.get_projects(mock_client, {}, {}, False)

        # Assert
        self.assertEqual(
            len(result_projects), len(test_projects), f"project size should be {len(test_projects)}"
        )

        # get_projects returns a list of (api_object, standardized_project). Use the standardized version for verification.
        result_project = result_projects[0][1]
        input_project = test_projects[0]
        self.assertEqual(
            result_project['id'], input_project['id'], "resulting project id does not match input"
        )
        self.assertEqual(
            result_project['login'],
            input_project['key'],
            "resulting project login does not match input",
        )
        self.assertEqual(
            result_project['name'],
            input_project['name'],
            "resulting project name does not match input",
        )
        self.assertEqual(
            result_project['url'],
            input_project['links']['self'][0]['href'],
            "resulting project url does not match input",
        )

    def test_get_repos(self):
        # Arrange
        test_projects = _get_test_data('test_projects.json')
        test_repos = _get_test_data('test_repos.json')
        test_branches = _get_test_data('test_branches.json')

        mock_client = MagicMock()
        mock_project = MagicMock()
        mock_repo = MagicMock()
        mock_branch = MagicMock()
        mock_repo_list = [mock_repo]

        mock_client.projects = {'test_project_key': mock_project}
        mock_project.repos.list.return_value = mock_repo_list
        mock_project.repos.__getitem__.return_value = mock_repo

        mock_repo.get.return_value = test_repos[0]
        mock_repo.branches.return_value = test_branches
        mock_repo.default_branch = mock_branch

        default_branch_name = 'default_branch_name'
        mock_branch.__getitem__.return_value = default_branch_name

        # Act
        result_repos = list(bitbucket_server.get_repos(mock_client, test_projects, {}, {}, False))

        # Assert
        self.assertEqual(
            len(result_repos), len(test_repos), f"repo size should be {len(test_repos)}"
        )
        self.assertEqual(
            result_repos[0][0], mock_repo, "resulting tuple should have input mock as first element"
        )

        # get_repos returns a list of (api_object, standardized_project). Use the standardized version for verification.
        result_repo = result_repos[0][1]
        input_repo = test_repos[0]
        self.assertEqual(
            result_repo['id'], input_repo['id'], "resulting repo id does not match input"
        )
        self.assertEqual(
            result_repo['name'], input_repo['name'], "resulting repo name does not match input"
        )
        self.assertEqual(
            result_repo['full_name'],
            input_repo['name'],
            "resulting repo full_name does not match input",
        )
        self.assertEqual(
            result_repo['url'],
            input_repo['links']['self'][0]['href'],
            "resulting repo url does not match input",
        )
        self.assertEqual(
            result_repo['default_branch_name'],
            default_branch_name,
            "resulting repo has unexpected default branch",
        )
        self.assertFalse(result_repo['is_fork'])

        # Assert expected branches exist
        self.assertEqual(
            len(result_repo['branches']),
            len(test_branches),
            f"resulting repo should have {len(test_branches)} branches",
        )
        for idx, test_branch in enumerate(test_branches):
            result_branch = result_repo['branches'][idx]
            self.assertEqual(
                result_branch['name'],
                test_branch['displayId'],
                "resulting branch name does not match input",
            )
            self.assertEqual(
                result_branch['sha'],
                test_branch['latestCommit'],
                "resulting branch sha does not match input",
            )

    def test_get_branch_commits(self):
        # Arrange
        test_repos = _get_test_data('test_repos.json')
        test_branches = _get_test_data('test_branches.json')
        test_commits = _get_test_data('test_commits.json')

        mock_client = MagicMock()
        mock_project = MagicMock()
        mock_api_repo = MagicMock()
        mock_api_repos = [mock_api_repo]

        mock_api_repo.get.return_value = test_repos[0]
        mock_client.projects = {'test_project_key': mock_project}

        mock_api_repo.default_branch = test_branches[0]
        mock_project.repos = {'test_repo_name': mock_api_repo}
        mock_api_repo.commits.return_value = test_commits

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act
        result_commits = list(
            bitbucket_server.get_commits_for_included_branches(
                mock_client,
                mock_api_repos,
                {'test_repo_name': ['test_display_id1']},
                False,
                test_git_instance_info,
                False,
                False,
            )
        )

        # Assert
        self.assertEqual(
            len(result_commits), len(test_commits), f"commit size should be {len(test_commits)}"
        )
        for idx, test_commit in enumerate(test_commits):
            result_commit = result_commits[idx]
            self.assertEqual(
                result_commit['hash'],
                test_commit['id'],
                "resulting commit hash does not match input",
            )
            self.assertEqual(
                result_commit['author']['email'],
                test_commit['author']['emailAddress'],
                "resulting author email does not match input",
            )
            self.assertEqual(
                result_commit['author']['login'],
                test_commit['author']['name'],
                "resulting author login does not match input",
            )
            expected_url = test_repos[0]['links']['self'][0]['href'].replace(
                'browse', f'commits/{test_commit["id"]}'
            )
            self.assertEqual(
                result_commit['url'], expected_url, "resulting url does not match input"
            )
            self.assertEqual(
                result_commit['message'],
                test_commit['message'],
                "resulting message does not match input",
            )
            self.assertFalse(result_commit['is_merge'])

            expected_repo = test_repos[0]

            self.assertEqual(
                result_commit['repo']['id'],
                expected_repo['id'],
                "resulting repo id does not match input",
            )
            self.assertEqual(
                result_commit['repo']['name'],
                expected_repo['name'],
                "resulting repo name does not match input",
            )
            self.assertEqual(
                result_commit['repo']['url'],
                expected_repo['links']['self'][0]['href'],
                "resulting repo links do not match input",
            )
            self.assertNotIn(
                'emailAddress',
                result_commit['author'].keys(),
                "author field of commit was not standardized; 'emailAddress' not renamed to 'email'",
            )
            self.assertIn(
                'login',
                result_commit['author'].keys(),
                "author field of commit was not standardized; 'login' not present as username key",
            )

    def test_get_pull_requests(self):
        # Arrange
        test_prs = _get_test_data('test_prs.json')
        test_repos = _get_test_data('test_repos.json')
        test_commits = _get_test_data('test_commits.json')

        mock_client = MagicMock()
        mock_project = MagicMock()
        mock_api_repo = MagicMock()
        mock_api_repos = [mock_api_repo]

        mock_api_repo.get.return_value = test_repos[0]
        mock_api_repo.pull_requests.all.return_value = test_prs

        mock_client.projects = {'test_project_key': mock_project}

        mock_project.repos = {'test_repo_name': mock_api_repo}
        mock_api_repo.commits.return_value = test_commits

        # Set pull_from to very far in the past to ensure fake timestamps in test commits are after this date.
        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act
        result_prs = list(
            bitbucket_server.get_pull_requests(
                mock_client, mock_api_repos, False, test_git_instance_info, False, False
            )
        )

        # Assert
        self.assertEqual(len(result_prs), len(test_prs), f"PR size should be {len(test_prs)}")
        result_pr = result_prs[0]
        input_pr = test_prs[0]
        self.assertEqual(result_pr['id'], input_pr['id'], "resulting PR id does not match input")
        self.assertEqual(
            result_pr['author']['name'],
            input_pr['author']['user']['displayName'],
            "resulting PR author name does not match input",
        )
        self.assertEqual(
            result_pr['title'], input_pr['title'], "resulting PR title does not match input"
        )
        self.assertEqual(
            result_pr['body'], input_pr['description'], "resulting PR body does not match input"
        )
        self.assertTrue(result_pr['is_closed'])
        self.assertFalse(result_pr['is_merged'])
        self.assertEqual(
            result_pr['url'],
            input_pr['links']['self'][0]['href'],
            "resulting PR url does not match input",
        )
        self.assertEqual(
            result_pr['base_repo']['name'],
            input_pr['toRef']['repository']['name'],
            "resulting PR base repo name does not match input",
        )
        self.assertEqual(
            result_pr['base_branch'],
            input_pr['toRef']['displayId'],
            "resulting PR base branch does not match input",
        )
        self.assertEqual(
            result_pr['head_repo']['name'],
            input_pr['fromRef']['repository']['name'],
            "resulting PR repo name does not match input",
        )
        self.assertEqual(
            result_pr['head_branch'],
            input_pr['fromRef']['displayId'],
            "resulting PR body does not match input",
        )


def _get_test_data(file_name):
    with open(f'{TEST_INPUT_FILE_PATH}{file_name}', 'r') as f:
        return json.loads(f.read())


if __name__ == "__main__":
    unittest.main()
