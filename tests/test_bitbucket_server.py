import json
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

import stashy.errors
from requests.exceptions import RetryError

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

        mock_client.projects = {'test_project_key': mock_project}
        # The listing returns whole repo objects, which is what the code now uses.
        mock_project.repos.paginate.return_value = [test_repos[0]]
        mock_project.repos.__getitem__.return_value = mock_repo

        mock_repo.paginate.return_value = test_branches
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
            result_repos[0][0].resource,
            mock_repo,
            "resulting tuple should have input mock as first element",
        )
        self.assertEqual(
            result_repos[0][0].data,
            test_repos[0],
            "the listing's own repo object should be carried forward",
        )
        mock_repo.get.assert_not_called()

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
        mock_api_repos = [bitbucket_server.ApiRepo(mock_api_repo, test_repos[0])]

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
        mock_api_repos = [bitbucket_server.ApiRepo(mock_api_repo, test_repos[0])]

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

    def test_get_repos_skips_deleted_repo(self):
        # A repo deleted between the listing and here answers 404 on both branch
        # endpoints. An empty but live repo answers 404 only on the default
        # branch, so both together are what tell the two apart.
        test_projects = _get_test_data('test_projects.json')
        test_repos = _get_test_data('test_repos.json')
        test_branches = _get_test_data('test_branches.json')

        mock_client = MagicMock()
        mock_project = MagicMock()

        mock_live_repo = MagicMock()
        mock_live_repo.paginate.return_value = test_branches
        mock_live_repo.default_branch = MagicMock()

        mock_client.projects = {'test_project_key': mock_project}
        mock_project.repos.paginate.return_value = [test_repos[0], test_repos[0]]
        mock_project.repos.__getitem__.side_effect = [_DeletedRepo(), mock_live_repo]

        # Act -- should not raise despite first repo being deleted
        result_repos = list(bitbucket_server.get_repos(mock_client, test_projects, {}, {}, False))

        # Assert -- deleted repo skipped, live repo still returned
        self.assertEqual(len(result_repos), 1)
        self.assertEqual(result_repos[0][0].resource, mock_live_repo)

    def test_get_repos_keeps_an_empty_repo(self):
        # Only the default branch 404s, so the repo is live but empty and must
        # still be returned rather than silently dropped.
        test_projects = _get_test_data('test_projects.json')
        test_repos = _get_test_data('test_repos.json')

        mock_client = MagicMock()
        mock_project = MagicMock()
        mock_client.projects = {'test_project_key': mock_project}
        mock_project.repos.paginate.return_value = [test_repos[0]]
        mock_project.repos.__getitem__.return_value = _EmptyRepo()

        result_repos = list(bitbucket_server.get_repos(mock_client, test_projects, {}, {}, False))

        self.assertEqual(len(result_repos), 1)
        self.assertEqual(result_repos[0][1]['default_branch_name'], '')
        self.assertEqual(result_repos[0][1]['branches'], [])

    def test_get_branch_commits_skips_deleted_repo(self):
        # A repo deleted mid-run now shows up as a 404 from the commits fetch
        # itself rather than from a refetch of the repo, and must not stop the
        # run or lose the repos after it.
        test_projects = _get_test_data('test_projects.json')
        test_repos = _get_test_data('test_repos.json')
        test_branches = _get_test_data('test_branches.json')
        test_commits = _get_test_data('test_commits.json')

        deleted_repo = {**test_repos[0], 'name': 'deleted_repo_name', 'id': 999}

        mock_client = MagicMock()
        mock_project = MagicMock()

        mock_deleted_repo = MagicMock()
        mock_deleted_repo.default_branch = test_branches[0]
        mock_deleted_repo.commits.side_effect = stashy.errors.NotFoundException(MagicMock())

        mock_live_repo = MagicMock()
        mock_live_repo.default_branch = test_branches[0]
        mock_live_repo.commits.return_value = test_commits

        mock_client.projects = {'test_project_key': mock_project}
        mock_project.repos = {
            'test_repo_name': mock_live_repo,
            'deleted_repo_name': mock_deleted_repo,
        }

        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act -- should not raise despite the first repo being deleted
        result = list(
            bitbucket_server.get_commits_for_included_branches(
                mock_client,
                [
                    bitbucket_server.ApiRepo(mock_deleted_repo, deleted_repo),
                    bitbucket_server.ApiRepo(mock_live_repo, test_repos[0]),
                ],
                {},
                False,
                test_git_instance_info,
                False,
                False,
            )
        )

        # Assert -- the live repo's commits still come through
        self.assertEqual(len(result), len(test_commits))

    def test_get_pull_requests_skips_deleted_repo(self):
        # pull_requests.all() pages lazily, so a deleted repo raises on its
        # first page. Unhandled that would end the whole PR download.
        test_projects = _get_test_data('test_projects.json')
        test_repos = _get_test_data('test_repos.json')
        test_prs = _get_test_data('test_prs.json')

        deleted_repo = {**test_repos[0], 'name': 'deleted_repo_name', 'id': 999}

        mock_client = MagicMock()
        mock_project = MagicMock()

        mock_deleted_repo = MagicMock()
        mock_deleted_repo.pull_requests.all.side_effect = stashy.errors.NotFoundException(
            MagicMock()
        )

        mock_live_repo = MagicMock()
        mock_live_repo.pull_requests.all.return_value = test_prs

        mock_client.projects = {'test_project_key': mock_project}
        mock_project.repos = {
            'test_repo_name': mock_live_repo,
            'deleted_repo_name': mock_deleted_repo,
        }

        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act -- should not raise despite the first repo being deleted
        result = list(
            bitbucket_server.get_pull_requests(
                mock_client,
                [
                    bitbucket_server.ApiRepo(mock_deleted_repo, deleted_repo),
                    bitbucket_server.ApiRepo(mock_live_repo, test_repos[0]),
                ],
                False,
                test_git_instance_info,
                False,
                False,
            )
        )

        # Assert -- the live repo's PRs still come through
        self.assertEqual(len(result), len(test_prs))

    def test_get_pull_requests_continues_when_pr_commits_fail(self):
        commit_errors = {
            'retries exhausted': RetryError('too many 500 error responses'),
            'authentication': stashy.errors.AuthenticationException(MagicMock(status_code=401)),
        }
        for error_name, commit_error in commit_errors.items():
            with self.subTest(error=error_name):
                self._assert_pr_emitted_without_commits_on_error(commit_error)

    def _assert_pr_emitted_without_commits_on_error(self, commit_error):
        # Arrange
        test_prs = _get_test_data('test_prs.json')
        test_prs.append({**test_prs[0], 'id': test_prs[0]['id'] + 1})
        test_repos = _get_test_data('test_repos.json')
        test_commits = _get_test_data('test_commits.json')

        mock_client = MagicMock()
        mock_project = MagicMock()
        mock_api_repo = MagicMock()

        mock_api_repo.get.return_value = test_repos[0]
        mock_api_repo.pull_requests.all.return_value = test_prs
        mock_client.projects = {'test_project_key': mock_project}
        mock_project.repos = {'test_repo_name': mock_api_repo}

        # The first PR's commits endpoint fails; every other PR's commits succeed.
        failing_pr_id = test_prs[0]['id']
        api_prs = {}
        for pr in test_prs:
            api_pr = MagicMock()
            if pr['id'] == failing_pr_id:
                api_pr.commits.side_effect = commit_error
            else:
                api_pr.commits.return_value = test_commits
            api_prs[pr['id']] = api_pr
        mock_api_repo.pull_requests.__getitem__.side_effect = lambda pr_id: api_prs[pr_id]

        test_git_instance_info = {'pull_from': '1900-07-23', 'repos_dict_v2': {}}

        # Act — should not raise despite the commits fetch failing
        result_prs = list(
            bitbucket_server.get_pull_requests(
                mock_client, [mock_api_repo], False, test_git_instance_info, False, False
            )
        )

        # Assert — the failing PR is still emitted without commits, and the rest keep theirs
        self.assertEqual(len(result_prs), len(test_prs))
        results_by_id = {pr['id']: pr for pr in result_prs}
        self.assertEqual(results_by_id[failing_pr_id]['commits'], [])
        for pr_id, result_pr in results_by_id.items():
            if pr_id != failing_pr_id:
                self.assertEqual(len(result_pr['commits']), len(test_commits))


class TestBitbucketServerRepoListing(TestCase):
    """Cover how get_repos enumerates repos: paged listing versus per-repo point reads."""

    def setUp(self):
        self.test_projects = _get_test_data('test_projects.json')
        self.test_repo = _get_test_data('test_repos.json')[0]
        self.test_branches = _get_test_data('test_branches.json')

        self.mock_project = MagicMock()
        self.mock_client = MagicMock()
        self.mock_client.projects = {'test_project_key': self.mock_project}

    def _mock_repo(self, get_error=None):
        mock_repo = MagicMock()
        if get_error:
            mock_repo.get.side_effect = get_error
        else:
            mock_repo.get.return_value = self.test_repo
        mock_repo.paginate.return_value = self.test_branches
        return mock_repo

    def _repos_by_name(self, repos_by_name):
        self.mock_project.repos.__getitem__.side_effect = lambda name: repos_by_name[name]

    def _run(self, include_repos):
        return list(
            bitbucket_server.get_repos(
                self.mock_client, self.test_projects, include_repos, {}, False
            )
        )

    def test_listing_asks_for_a_full_page(self):
        """Bitbucket Server defaults to 25 repos per page, which is ~640 requests for 16k repos."""
        self.mock_project.repos.paginate.return_value = [self.test_repo]
        self.mock_project.repos.__getitem__.return_value = self._mock_repo()

        self._run(include_repos={})

        self.mock_project.repos.paginate.assert_called_once_with(
            '', params={'limit': bitbucket_server.REPO_PAGE_SIZE}
        )

    def test_allowlist_too_large_for_point_reads_pages_the_listing(self):
        """Point reads cost one request per entry per project, so a big allowlist pages instead."""
        self.mock_project.repos.paginate.return_value = [self.test_repo]
        self.mock_project.repos.__getitem__.return_value = self._mock_repo()

        self._run(include_repos=[f'repo-{i}' for i in range(bitbucket_server.MAX_POINT_READS + 1)])

        self.mock_project.repos.paginate.assert_called_once()

    def test_allowlist_entry_that_is_not_a_slug_pages_the_listing(self):
        """A point read addresses a slug, so a name that is not one would find no repo."""
        self.mock_project.repos.paginate.return_value = [self.test_repo]
        self.mock_project.repos.__getitem__.return_value = self._mock_repo()

        repos = self._run(include_repos=['test_repo_name', 'Name With Spaces'])

        self.mock_project.repos.paginate.assert_called_once()
        self.assertEqual([r[1]['name'] for r in repos], ['test_repo_name'])

    def test_point_read_skips_a_repo_it_cannot_fetch(self):
        """Allowlist entries span projects, so a failed read is routine and must not end the run."""
        for error in (
            stashy.errors.NotFoundException(MagicMock()),
            stashy.errors.AuthenticationException(MagicMock()),
            stashy.errors.GenericException(MagicMock()),
        ):
            with self.subTest(error=type(error).__name__):
                self.mock_project.reset_mock()
                self._repos_by_name(
                    {
                        'other-repo': self._mock_repo(get_error=error),
                        'test_repo_name': self._mock_repo(),
                    }
                )

                repos = self._run(include_repos=['other-repo', 'test_repo_name'])

                self.mock_project.repos.paginate.assert_not_called()
                self.assertEqual([r[1]['name'] for r in repos], ['test_repo_name'])


def _get_test_data(file_name):
    with open(f'{TEST_INPUT_FILE_PATH}{file_name}', 'r') as f:
        return json.loads(f.read())


class _DeletedRepo:
    """A repo that has gone away: every branch endpoint answers 404."""

    @property
    def default_branch(self):
        raise stashy.errors.NotFoundException(MagicMock())

    def paginate(self, *args, **kwargs):
        raise stashy.errors.NotFoundException(MagicMock())


class _EmptyRepo:
    """A live repo with no commits: no default branch, but branches lists fine."""

    @property
    def default_branch(self):
        raise stashy.errors.NotFoundException(MagicMock())

    def paginate(self, *args, **kwargs):
        return []


class _RecordingRepo:
    """A Bitbucket Server repo that records how it was read.

    MagicMock cannot see a plain attribute read, and the request count is the
    thing under test here, so these two reads are counted explicitly.
    """

    def __init__(self, repo, branches):
        self._repo = repo
        self._branches = branches
        self.default_branch_reads = 0
        self.paginate_calls = []
        self.unpaginated_branches_calls = 0
        self.get_calls = 0

    def get(self):
        self.get_calls += 1
        return self._repo

    @property
    def default_branch(self):
        self.default_branch_reads += 1
        return {'displayId': 'main'}

    def branches(self, *args, **kwargs):
        # stashy's own helper, which sends no limit. Present so that falling
        # back to it fails the assertion below rather than an AttributeError.
        self.unpaginated_branches_calls += 1
        return self._branches

    def paginate(self, resource_url, params=None):
        self.paginate_calls.append((resource_url, params))
        return self._branches


class TestBitbucketServerRepoRequestCount(TestCase):
    """Each extra read here is paid once per repo, so a 16,000 repo instance
    pays for it 16,000 times."""

    def setUp(self):
        self.test_projects = _get_test_data('test_projects.json')
        self.test_repos = _get_test_data('test_repos.json')
        self.test_branches = _get_test_data('test_branches.json')
        self.api_repo = _RecordingRepo(self.test_repos[0], self.test_branches)

    def _standardize(self):
        return bitbucket_server._standardize_repo(
            self.test_projects[0], self.api_repo, False, self.test_repos[0]
        )

    def test_the_repo_is_not_refetched(self):
        # The listing already returned this object; refetching it cost one
        # request per repo in each of the three phases.
        self._standardize()

        self.assertEqual(self.api_repo.get_calls, 0)

    def test_default_branch_is_read_once(self):
        # default_branch is an uncached property, so a second read is a second
        # request for an answer already held.
        self._standardize()

        self.assertEqual(self.api_repo.default_branch_reads, 1)

    def test_branches_are_requested_a_full_page_at_a_time(self):
        # stashy's branches() sends no limit, so Bitbucket Server pages at 25.
        self._standardize()

        self.assertEqual(
            self.api_repo.paginate_calls,
            [('/branches', {'limit': bitbucket_server.BRANCH_PAGE_SIZE})],
        )

    def test_the_commits_phase_also_reads_default_branch_once(self):
        # _get_default_branch_name is called once per repo by the commits phase
        # and carried the same double read, so the saving here is the same size.
        bitbucket_server._get_default_branch_name(self.api_repo)

        self.assertEqual(self.api_repo.default_branch_reads, 1)

    def test_the_standardized_repo_is_unchanged(self):
        # The reads above are fewer, but what they produce must not change.
        result = self._standardize()

        self.assertEqual(result['id'], self.test_repos[0]['id'])
        self.assertEqual(result['name'], self.test_repos[0]['name'])
        self.assertEqual(result['default_branch_name'], 'main')
        self.assertEqual(
            [b['name'] for b in result['branches']],
            [b['displayId'] for b in self.test_branches],
        )


if __name__ == "__main__":
    unittest.main()
