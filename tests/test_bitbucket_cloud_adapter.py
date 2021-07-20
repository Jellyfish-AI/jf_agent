import unittest
from unittest import TestCase
from unittest.mock import Mock

from jf_agent.git.bitbucket_cloud_adapter import BitbucketCloudAdapter

class TestBitbucketCloudAdapter(TestCase):

    def setUp(self):
        mock_config = Mock()
        mock_git_connection = Mock()
        outdir = "test"
        self.adapter = BitbucketCloudAdapter(
                mock_config, outdir, False, mock_git_connection
            )

    def test_get_users(self):
        self.assertEqual(self.adapter.get_users(), [], "Should be an empty user list")

    def test_sum(self):
        self.assertEqual(sum([1, 2, 3]), 6, "Should be 6")



if __name__ == "__main__":
    unittest.main()