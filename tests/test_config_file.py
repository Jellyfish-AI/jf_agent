import json
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

import yaml
from jf_ingest.config import AzureDevopsAuthConfig as JFIngestAzureDevopsAuthConfig

from jf_agent.config_file_reader import (
    GitConfig,
    _get_git_config_from_yaml,
    _get_jf_ingest_git_auth_config,
)


class TestGitConfigGeneration(TestCase):
    def test_get_git_config_from_yaml_ado_default(self):
        ado_yaml_content = """
        git:
            provider: ado
            url: https://ado.com
            verbose: true
        """

        yaml_config = yaml.safe_load(ado_yaml_content)

        git_configs: list[GitConfig] = _get_git_config_from_yaml(yaml_config)

        self.assertEqual(len(git_configs), 1)
        git_config = git_configs[0]
        assert git_config.git_provider == 'ado'
        assert git_config.git_url == 'https://ado.com'
        assert git_config.git_verbose is True
        assert git_config.ado_api_version is None  # Default value

    def test_get_git_config_from_yaml_ado_version_override(self):
        ado_yaml_content = """
        git:
            provider: ado
            url: https://ado.com
            verbose: true
            ado_api_version: '6.0'
        """

        yaml_config = yaml.safe_load(ado_yaml_content)

        git_configs: list[GitConfig] = _get_git_config_from_yaml(yaml_config)

        self.assertEqual(len(git_configs), 1)
        git_config = git_configs[0]
        assert git_config.git_provider == 'ado'
        assert git_config.git_url == 'https://ado.com'
        assert git_config.git_verbose is True
        assert git_config.ado_api_version == '6.0'

    def test_get_git_config_multi_provider(self):
        ado_yaml_content = """
        git:
            - provider: ado
              creds_envvar_prefix: ORG1
              instance_slug: ado-instance-1
              url: https://ado.com
              verbose: true
              ado_api_version: '6.0'
            - provider: ado
              creds_envvar_prefix: ORG2
              instance_slug: ado-instance-2
              url: https://ado.com
              verbose: true
        """

        yaml_config = yaml.safe_load(ado_yaml_content)

        git_configs: list[GitConfig] = _get_git_config_from_yaml(yaml_config)
        git_configs = sorted(git_configs, key=lambda x: x.git_instance_slug)

        self.assertEqual(len(git_configs), 2)

        config_1 = git_configs[0]
        assert config_1.git_instance_slug == 'ado-instance-1'
        assert config_1.git_provider == 'ado'
        assert config_1.git_url == 'https://ado.com'
        assert config_1.git_verbose is True
        assert config_1.ado_api_version == '6.0'

        config_2 = git_configs[1]
        assert config_2.git_instance_slug == 'ado-instance-2'
        assert config_2.git_provider == 'ado'
        assert config_2.git_url == 'https://ado.com'
        assert config_2.git_verbose is True
        assert config_2.ado_api_version is None  # Default value

    def test_get_jf_ingest_git_auth_config(self):
        ado_yaml_content = """
        git:
            - provider: ado
              creds_envvar_prefix: ORG1
              instance_slug: ado-instance-1
              url: https://ado.com
              verbose: true
              ado_api_version: '6.0'
            - provider: ado
              creds_envvar_prefix: ORG2
              instance_slug: ado-instance-2
              url: https://ado.com
              verbose: true
        """

        yaml_config = yaml.safe_load(ado_yaml_content)

        git_configs: list[GitConfig] = _get_git_config_from_yaml(yaml_config)
        git_configs = sorted(git_configs, key=lambda x: x.git_instance_slug)

        # Test first config
        auth_config = _get_jf_ingest_git_auth_config(
            company_slug='test-company',
            config=git_configs[0],
            git_creds={'ado_token': 'token-1'},
            skip_ssl_verification=True,
        )
        assert type(auth_config) == JFIngestAzureDevopsAuthConfig
        assert auth_config.company_slug == 'test-company'
        assert auth_config.token == 'token-1'
        auth_config.api_version == '6.0'
        assert auth_config.verify is False

        # Test second config
        auth_config = _get_jf_ingest_git_auth_config(
            company_slug='test-company',
            config=git_configs[1],
            git_creds={'ado_token': 'token-2'},
            skip_ssl_verification=False,
        )
        assert type(auth_config) == JFIngestAzureDevopsAuthConfig
        assert auth_config.company_slug == 'test-company'
        assert auth_config.token == 'token-2'
        auth_config.api_version == '7.0'
        assert auth_config.verify is True

    def test_get_jf_ingest_git_auth_config_convert_to_str(self):
        ado_yaml_content = """
        git:
            - provider: ado
              creds_envvar_prefix: ORG1
              instance_slug: ado-instance-1
              url: https://ado.com
              verbose: true
              ado_api_version: 7.1
            - provider: ado
              creds_envvar_prefix: ORG2
              instance_slug: ado-instance-2
              url: https://ado.com
              verbose: true
        """

        yaml_config = yaml.safe_load(ado_yaml_content)

        git_configs: list[GitConfig] = _get_git_config_from_yaml(yaml_config)
        git_configs = sorted(git_configs, key=lambda x: x.git_instance_slug)

        # Test first config
        auth_config = _get_jf_ingest_git_auth_config(
            company_slug='test-company',
            config=git_configs[0],
            git_creds={'ado_token': 'token-1'},
            skip_ssl_verification=True,
        )
        assert type(auth_config) == JFIngestAzureDevopsAuthConfig
        assert auth_config.company_slug == 'test-company'
        assert auth_config.token == 'token-1'
        assert auth_config.api_version == '7.1'
        assert auth_config.verify is False

        # Test second config
        auth_config = _get_jf_ingest_git_auth_config(
            company_slug='test-company',
            config=git_configs[1],
            git_creds={'ado_token': 'token-2'},
            skip_ssl_verification=False,
        )
        assert type(auth_config) == JFIngestAzureDevopsAuthConfig
        assert auth_config.company_slug == 'test-company'
        assert auth_config.token == 'token-2'
        assert auth_config.api_version == '7.0'
        assert auth_config.verify is True
