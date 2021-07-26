import json
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

from jf_agent.git import github

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
            self.assertEqual(resulting_user.login, input_user.get('login'), "user logins should match")
            self.assertEqual(resulting_user.name, input_user.get('name'), "user names should match")
            self.assertEqual(resulting_user.email, input_user.get('email'), "user emails should match")
        
    def test_get_projects(self):
        # Arrange
        test_projects = _get_test_data('test_projects.json')
        mock_client = MagicMock()
        mock_client.get_organization_by_name.return_value = test_projects[0]

        # Act
        result_projects = github.get_projects(mock_client, ['test_org'], False)
        
        # Assert
        self.assertEqual(len(result_projects), 1, "project size should be 1")
        
        # get_projects returns a list of (api_object, normalized_project). Use the normalized version for verification.
        result_project = result_projects[0]
        input_project = test_projects[0]
        self.assertEqual(result_project.id, input_project['id'], "resulting project id does not match input")
        self.assertEqual(result_project.login, input_project['login'], "resulting project login does not match input")
        self.assertEqual(result_project.name, input_project['name'], "resulting project name does not match input")
        self.assertEqual(result_project.url, input_project['html_url'], "resulting project url does not match input")
    
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

        # get_repos returns a list of (api_object, normalized_project). Use the normalized version for verification.
        result_repo = result_repos[0][1]
        test_repo = test_repos[0]
        self.assertEqual(result_repo.id, test_repo['id'], "resulting repo id does not match input")
        self.assertEqual(result_repo.name, test_repo['name'], "resulting repo name does not match input")
        self.assertEqual(result_repo.full_name, test_repo['full_name'], "resulting repo full_name does not match input")
        self.assertEqual(result_repo.url, test_repo['html_url'], "resulting repo url does not match input")
        self.assertEqual(result_repo.default_branch_name, test_repo['default_branch'], "resulting repo has unexpected default branch")
        self.assertFalse(result_repo.is_fork)
    
        # Assert expected branches exist
        self.assertEqual(len(result_repo.branches), len(test_branches), f"resulting repo should have {len(test_branches)} branches")
        for idx, test_branch in enumerate(test_branches):
            result_branch = result_repo.branches[idx]
            self.assertEqual(result_branch.name, test_branch['name'], "resulting branch name does not match input")
            self.assertEqual(result_branch.sha, test_branch['commit']['sha'], "resulting branch sha does not match input")


    def test_get_branch_commits(self):
        # Arrange
        return None
    def test_get_pull_requests(self):
        # Arrange
        return None

def _get_test_data(file_name):
    with open(f'tests/test_data/github/{file_name}', 'r') as f:
        return json.loads(f.read())

if __name__ == "__main__":
    unittest.main()