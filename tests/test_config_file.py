import json
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

import yaml
from jf_ingest.config import AzureDevopsAuthConfig as JFIngestAzureDevopsAuthConfig

from jf_agent.config_file_reader import (
    GitConfig,
    _get_git_config_from_yaml,
    _get_jf_ingest_git_auth_config,
    get_ingest_config,
)


def _build_ado_git_config() -> GitConfig:
    return GitConfig(
        git_url='https://ado.com',
        git_provider='ado',
        git_instance_slug='ado-instance-1',
        git_include_projects=[],
        git_exclude_projects=[],
        git_include_all_repos_inside_projects=[],
        git_exclude_all_repos_inside_projects=[],
        git_include_repos=[],
        git_exclude_repos=[],
        git_include_branches={},
        git_strip_text_content=False,
        git_redact_names_and_urls=False,
        gitlab_per_page_override=False,
        git_verbose=False,
        gitlab_keep_base_url=False,
        creds_envvar_prefix='ORG1',
        git_include_bbcloud_projects=[],
        git_exclude_bbcloud_projects=[],
    )


def _build_ingest_config_inputs(endpoint_git_instance_info: dict):
    config = MagicMock()
    config.jira_url = None
    config.git_configs = [_build_ado_git_config()]
    config.skip_ssl_verification = False
    config.run_mode_includes_send = False
    config.jira_skip_saving_data_locally = False
    config.outdir = 'agent-output-test'
    config.jellyfish_api_base = 'https://api.jellyfish.co'

    creds = MagicMock()
    creds.git_instance_to_creds = {'ado-instance-1': {'ado_token': 'token-1'}}
    creds.jellyfish_api_token = 'jf-token'

    endpoint_git_instances_info = {'ado-instance-1': endpoint_git_instance_info}

    return config, creds, endpoint_git_instances_info


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


class TestBackpopulationWindowDaysPassthrough(TestCase):
    """
    Verifies the optional `backpopulation_window_days` value from the server endpoint
    is forwarded into JFIngestGitConfig only when present, so that jf-ingest's default
    applies otherwise.
    """

    @patch('jf_agent.config_file_reader.IngestionConfig')
    @patch('jf_agent.config_file_reader.JFIngestGitConfig')
    @patch('jf_agent.config_file_reader._get_jf_ingest_git_auth_config')
    @patch('jf_agent.config_file_reader.get_company_info')
    def test_backpopulation_window_days_forwarded_when_present(
        self,
        mock_get_company_info,
        mock_get_auth,
        mock_jf_ingest_git_config,
        mock_ingestion_config,
    ):
        mock_get_company_info.return_value = {'company_slug': 'test-co'}
        mock_get_auth.return_value = MagicMock()

        config, creds, endpoint_git_instances_info = _build_ingest_config_inputs(
            endpoint_git_instance_info={
                'slug': 'ado-instance-1',
                'key': 'ado-key',
                'repos_dict_v2': {},
                'pull_from': '2024-01-01T00:00:00',
                'backpopulation_window_days': 90,
            }
        )

        get_ingest_config(
            config=config,
            creds=creds,
            endpoint_jira_info={},
            endpoint_git_instances_info=endpoint_git_instances_info,
            jf_options={},
        )

        self.assertEqual(mock_jf_ingest_git_config.call_count, 1)
        kwargs = mock_jf_ingest_git_config.call_args.kwargs
        self.assertEqual(kwargs.get('backpopulation_window_days'), 90)

    @patch('jf_agent.config_file_reader.IngestionConfig')
    @patch('jf_agent.config_file_reader.JFIngestGitConfig')
    @patch('jf_agent.config_file_reader._get_jf_ingest_git_auth_config')
    @patch('jf_agent.config_file_reader.get_company_info')
    def test_backpopulation_window_days_omitted_when_absent(
        self,
        mock_get_company_info,
        mock_get_auth,
        mock_jf_ingest_git_config,
        mock_ingestion_config,
    ):
        mock_get_company_info.return_value = {'company_slug': 'test-co'}
        mock_get_auth.return_value = MagicMock()

        config, creds, endpoint_git_instances_info = _build_ingest_config_inputs(
            endpoint_git_instance_info={
                'slug': 'ado-instance-1',
                'key': 'ado-key',
                'repos_dict_v2': {},
                'pull_from': '2024-01-01T00:00:00',
            }
        )

        get_ingest_config(
            config=config,
            creds=creds,
            endpoint_jira_info={},
            endpoint_git_instances_info=endpoint_git_instances_info,
            jf_options={},
        )

        self.assertEqual(mock_jf_ingest_git_config.call_count, 1)
        kwargs = mock_jf_ingest_git_config.call_args.kwargs
        self.assertNotIn('backpopulation_window_days', kwargs)
