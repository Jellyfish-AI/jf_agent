from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, date, timezone
import json
import logging
import os
from typing import List, Optional
import urllib3
import yaml

from jf_agent import JELLYFISH_API_BASE, VALID_RUN_MODES
from jf_agent.exception import BadConfigException
from jf_ingest import logging_helper
from jf_ingest.config import (
    IngestionConfig,
    IngestionType,
    IssueMetadata,
    JiraDownloadConfig,
    GitConfig as JFIngestGitConfig,
    GitAuthConfig as JFIngestGitAuthConfig,
)

from jf_agent.git.utils import JF_INGEST_SUPPORTED_PROVIDERS
from jf_agent.util import get_company_info

logger = logging.getLogger(__name__)


@dataclass
class GitConfig:
    git_url: str
    git_provider: str
    git_instance_slug: str
    git_include_projects: List
    git_exclude_projects: List
    git_include_all_repos_inside_projects: List
    git_exclude_all_repos_inside_projects: List
    git_include_repos: List
    git_exclude_repos: List
    git_include_branches: dict
    git_strip_text_content: bool
    git_redact_names_and_urls: bool
    gitlab_per_page_override: bool
    git_verbose: bool
    # For multi-git
    creds_envvar_prefix: str
    # legacy fields ==================
    git_include_bbcloud_projects: List
    git_exclude_bbcloud_projects: List


# todo convert to dataclass
ValidatedConfig = namedtuple(
    'ValidatedConfig',
    [
        'run_mode',
        'run_mode_includes_download',
        'run_mode_includes_send',
        'run_mode_is_print_all_jira_fields',
        'run_mode_is_print_apparently_missing_git_repos',
        'debug_http_requests',
        'jira_url',
        'jira_earliest_issue_dt',
        'jira_issue_download_concurrent_threads',
        'jira_include_fields',
        'jira_exclude_fields',
        'jira_issue_batch_size',
        'jira_gdpr_active',
        'jira_include_projects',
        'jira_exclude_projects',
        'jira_include_project_categories',
        'jira_exclude_project_categories',
        'jira_required_email_domains',
        'jira_is_email_required',
        'jira_issue_jql',
        'jira_download_worklogs',
        'jira_download_sprints',
        'jira_recursively_download_parents',
        'jira_skip_saving_data_locally',
        'git_configs',
        'outdir',
        'compress_output_files',
        'jellyfish_api_base',
        'jellyfish_webhook_base',
        'skip_ssl_verification',
        'send_agent_config',
        'git_max_concurrent',
        'skip_healthcheck_upload',
    ],
)

required_jira_fields = [
    'issuekey',
    'parent',
    'issuelinks',
    'project',
    'reporter',
    'assignee',
    'creator',
    'issuetype',
    'resolution',
    'resolutiondate',
    'status',
    'created',
    'updated',
    'subtasks',
]


def obtain_config(args) -> ValidatedConfig:
    if args.since:
        print(
            'WARNING: The -s / --since argument is deprecated and has no effect. You can remove its setting.'
        )
    if args.until:
        print(
            'WARNING: The -u / --until argument is deprecated and has no effect. You can remove its setting.'
        )

    jellyfish_api_base = args.jellyfish_api_base
    jellyfish_webhook_base = args.jellyfish_webhook_base
    config_file_path = args.config_file

    run_mode = args.mode
    if run_mode not in VALID_RUN_MODES:
        print(f'''ERROR: Mode should be one of "{', '.join(VALID_RUN_MODES)}"''')
        raise BadConfigException()

    run_mode_includes_download = run_mode in ('download_and_send', 'download_only')
    run_mode_includes_send = run_mode in ('download_and_send', 'send_only')
    run_mode_is_print_all_jira_fields = run_mode == 'print_all_jira_fields'
    run_mode_is_print_apparently_missing_git_repos = (
        run_mode == 'print_apparently_missing_git_repos'
    )

    debug_http_requests = args.debug_requests

    try:
        with open(config_file_path, 'r') as yaml_file:
            yaml_config = yaml.safe_load(yaml_file)
    except FileNotFoundError:
        print(f'ERROR: Config file not found at "{config_file_path}"')
        raise BadConfigException()

    yaml_conf_global = yaml_config.get('global', {})
    skip_ssl_verification = yaml_conf_global.get('no_verify_ssl', False)
    send_agent_config = yaml_conf_global.get('send_agent_config', False)
    skip_healthcheck_upload = yaml_conf_global.get('skip_healthcheck_upload', False)

    # jira configuration
    jira_config = yaml_config.get('jira', {})
    jira_url = jira_config.get('url', None)

    jira_earliest_issue_dt = jira_config.get('earliest_issue_dt', None)
    if jira_earliest_issue_dt is not None and type(jira_earliest_issue_dt) != date:
        print('ERROR: Invalid format for earliest_issue_dt; should be YYYY-MM-DD')
        raise BadConfigException()

    jira_issue_download_concurrent_threads = jira_config.get(
        'issue_download_concurrent_threads', 10
    )
    jira_include_fields = set(jira_config.get('include_fields', []))
    jira_exclude_fields = set(jira_config.get('exclude_fields', []))
    jira_issue_batch_size = jira_config.get('issue_batch_size', 100)
    jira_gdpr_active = jira_config.get('gdpr_active', False)
    jira_required_email_domains = set(jira_config.get('required_email_domains', []))
    jira_is_email_required = jira_config.get('is_email_required', False)
    jira_include_projects = set(jira_config.get('include_projects', []))
    jira_exclude_projects = set(jira_config.get('exclude_projects', []))
    jira_include_project_categories = set(jira_config.get('include_project_categories', []))
    jira_exclude_project_categories = set(jira_config.get('exclude_project_categories', []))
    jira_issue_jql = jira_config.get('issue_jql', '')
    jira_download_worklogs = jira_config.get('download_worklogs', True)
    jira_download_sprints = jira_config.get('download_sprints', True)
    jira_recursively_download_parents = jira_config.get('recursively_download_parents', False)
    jira_skip_saving_data_locally = jira_config.get('skip_saving_data_locally', False)

    # warn if any of the recommended fields are missing or excluded
    if jira_include_fields:
        missing_required_fields = set(required_jira_fields) - set(jira_include_fields)
        if missing_required_fields:
            logging_helper.log_standard_error(
                logging.WARNING, msg_args=[list(missing_required_fields)], error_code=2132,
            )
    if jira_exclude_fields:
        excluded_required_fields = set(required_jira_fields).intersection(set(jira_exclude_fields))
        if excluded_required_fields:
            logging_helper.log_standard_error(
                logging.WARNING, msg_args=[list(excluded_required_fields)], error_code=2142,
            )

    git_configs: List[GitConfig] = _get_git_config_from_yaml(yaml_config)
    git_max_concurrent = yaml_conf_global.get("git_max_concurrent", len(git_configs))

    now = datetime.utcnow()

    if not jira_url and not len(git_configs):
        print('ERROR: Config file must provide either a Jira or Git URL.')
        raise BadConfigException()

    if skip_ssl_verification:
        print('WARNING: Disabling SSL certificate validation')
        # To silence "Unverified HTTPS request is being made."
        urllib3.disable_warnings()

    if run_mode_includes_download:
        if args.prev_output_dir:
            print('ERROR: Provide output_basedir if downloading, not prev_output_dir')
            raise BadConfigException()

    output_basedir = args.output_basedir
    output_dir_timestamp = now.strftime('%Y%m%d_%H%M%S')
    outdir = os.path.join(output_basedir, output_dir_timestamp)
    try:
        os.makedirs(outdir, exist_ok=False)
    except FileExistsError:
        print(f"ERROR: Output dir {outdir} already exists")
        raise BadConfigException()
    except Exception:
        print(
            f"ERROR: Couldn't create output dir {outdir}.  Make sure the output directory you mapped as a docker volume exists on your host."
        )
        raise BadConfigException()

    if run_mode_is_print_all_jira_fields and not jira_url:
        print(f'ERROR: Must provide jira_url for mode {run_mode}')
        raise BadConfigException()

    if run_mode_includes_send and not run_mode_includes_download:
        if not args.prev_output_dir:
            print('ERROR: prev_output_dir must be provided if not downloading')
            raise BadConfigException()

        if not os.path.isdir(args.prev_output_dir):
            print(f'ERROR: prev_output_dir ("{args.prev_output_dir}") is not a directory')
            raise BadConfigException()

        outdir = args.prev_output_dir

    # If we're only downloading, do not compress the output files (so they can be more easily inspected)
    compress_output_files = (
        False if (run_mode_includes_download and not run_mode_includes_send) else True
    )

    if run_mode_is_print_apparently_missing_git_repos:
        if not len(git_configs):
            print(f'ERROR: {run_mode} requires git configuration.')
            raise BadConfigException()

        if not (jira_url and git_configs[0].git_url):
            print(f'ERROR: Must provide jira_url and git_url for mode {run_mode}')
            raise BadConfigException()

        for git_config in git_configs:
            if git_config.git_redact_names_and_urls:
                print(f'ERROR: git_redact_names_and_urls must be False for mode {run_mode}')
                raise BadConfigException()

    return ValidatedConfig(
        run_mode,
        run_mode_includes_download,
        run_mode_includes_send,
        run_mode_is_print_all_jira_fields,
        run_mode_is_print_apparently_missing_git_repos,
        debug_http_requests,
        jira_url,
        jira_earliest_issue_dt,
        jira_issue_download_concurrent_threads,
        jira_include_fields,
        jira_exclude_fields,
        jira_issue_batch_size,
        jira_gdpr_active,
        jira_include_projects,
        jira_exclude_projects,
        jira_include_project_categories,
        jira_exclude_project_categories,
        jira_required_email_domains,
        jira_is_email_required,
        jira_issue_jql,
        jira_download_worklogs,
        jira_download_sprints,
        jira_recursively_download_parents,
        jira_skip_saving_data_locally,
        git_configs,  # array of GitConfig
        outdir,
        compress_output_files,
        jellyfish_api_base,
        jellyfish_webhook_base,
        skip_ssl_verification,
        send_agent_config,
        git_max_concurrent,
        skip_healthcheck_upload,
    )


def _get_git_config_from_yaml(yaml_config) -> List[GitConfig]:
    # support legacy yaml configuration (where the key _is_ bitbucket)
    if 'bitbucket' in yaml_config:
        git_config = yaml_config.get('bitbucket', {})
        return [_get_git_config(git_config, 'bitbucket_server')]

    git_config = yaml_config.get('git')

    # support for no git instances
    if not git_config:
        return []

    # support for single git instance
    if not isinstance(git_config, list):
        return [_get_git_config(git_config)]

    # support for multiple git instances
    return [_get_git_config(g, multiple=True) for g in git_config]


git_providers = ['bitbucket_server', 'bitbucket_cloud', 'github', 'gitlab']


def _get_jf_ingest_git_auth_config(
    company_slug: str, config: GitConfig, git_creds: dict, skip_ssl_verification: bool,
):
    from jf_agent.git.utils import BBC_PROVIDER, BBS_PROVIDER, GH_PROVIDER, GL_PROVIDER

    try:
        if config.git_provider == BBS_PROVIDER:
            return None

        if config.git_provider == BBC_PROVIDER:
            return None

        if config.git_provider == GH_PROVIDER:
            return JFIngestGitAuthConfig(
                company_slug=company_slug,
                token=git_creds['github_token'],
                base_url=config.git_url,
                verify=not skip_ssl_verification,
            )
        if config.git_provider == GL_PROVIDER:
            return None

    except Exception as e:
        logging_helper.log_standard_error(
            logging.ERROR, msg_args=[config.git_provider, e], error_code=2101, exc_info=True,
        )
        return

    logging_helper.send_to_agent_log_file(
        f'Git Provider {config.git_provider} is not yet supported by JF Ingest'
    )


def get_ingest_config(
    config: ValidatedConfig,
    creds,
    endpoint_jira_info: dict,
    endpoint_git_instances_info: dict,
    jf_options: dict,
) -> IngestionConfig:
    """
    Handles converting our agent config to the jf_ingest IngestionConfig
    shared dataclass.
    """
    company_info = get_company_info(config, creds)

    company_slug = company_info.get('company_slug')

    jira_config: Optional[JiraDownloadConfig] = None
    # In the jellyfish API we are offsetting this value by x1000 and + 1, so we need to do the inverse here
    work_logs_timestamp = int((endpoint_jira_info.get('last_updated', 1) - 1) / 1000)
    if config.jira_url and (
        (creds.jira_username and creds.jira_password) or creds.jira_bearer_token
    ):
        jira_config: JiraDownloadConfig = JiraDownloadConfig(
            company_slug=company_slug,
            #
            # Auth Info
            url=config.jira_url,
            user=creds.jira_username if creds.jira_username else "",
            password=creds.jira_password if creds.jira_password else "",
            bypass_ssl_verification=config.skip_ssl_verification,
            personal_access_token=creds.jira_bearer_token if creds.jira_bearer_token else "",
            #
            # Server Info
            gdpr_active=config.jira_gdpr_active,
            #
            # Fields Info
            exclude_fields=config.jira_exclude_fields,
            include_fields=config.jira_include_fields,
            #
            # User Info
            required_email_domains=config.jira_required_email_domains,
            is_email_required=config.jira_is_email_required,
            #
            # Projects Info
            include_projects=config.jira_include_projects,
            exclude_projects=config.jira_exclude_projects,
            include_project_categories=config.jira_include_project_categories,
            exclude_project_categories=config.jira_exclude_project_categories,
            #
            # Sprints/Boards Info
            download_sprints=config.jira_download_sprints,
            #
            # Issues
            full_redownload=False,
            earliest_issue_dt=config.jira_earliest_issue_dt
            if config.jira_earliest_issue_dt
            else datetime.min,
            issue_download_concurrent_threads=config.jira_issue_download_concurrent_threads,
            jellyfish_issue_metadata=IssueMetadata.from_json(
                endpoint_jira_info.get('issue_metadata_for_jf_ingest', "[]")
            ),
            jellyfish_project_ids_to_keys=json.loads(
                endpoint_jira_info.get('jellyfish_project_ids_to_keys', "{}")
            ),
            skip_issues=False,
            only_issues=False,
            recursively_download_parents=config.jira_recursively_download_parents,
            #
            # worklogs
            download_worklogs=config.jira_download_worklogs,
            # we are passed the raw unix timestamp for work logs, but jf_ingest
            # expects a datetime. Do a quick conversion here
            work_logs_pull_from=datetime.fromtimestamp(work_logs_timestamp),
            # Jira Ingest Feature Flags
            # TODO: Generate from launchdarkly
            feature_flags={},
        )

    git_configs: List[JFIngestGitConfig] = []
    is_mult_git_mode = len(config.git_configs) > 1
    for agent_git_config in config.git_configs:
        agent_git_config: GitConfig = agent_git_config

        if agent_git_config.git_provider not in JF_INGEST_SUPPORTED_PROVIDERS:
            continue

        if is_mult_git_mode:
            instance_slug = agent_git_config.git_instance_slug
            endpoint_git_instance_info = endpoint_git_instances_info.get(instance_slug)
            instance_creds = creds.git_instance_to_creds.get(instance_slug)
        else:
            # If there's only one git config set, then this is "single git" mode.
            # The instance slug is likely not to be provided
            endpoint_git_instance_info = list(endpoint_git_instances_info.values())[0]
            instance_creds = list(creds.git_instance_to_creds.values())[0]
            instance_slug = endpoint_git_instance_info['slug']

        jf_ingest_git_auth_config = _get_jf_ingest_git_auth_config(
            company_slug=company_slug,
            config=agent_git_config,
            git_creds=instance_creds,
            skip_ssl_verification=config.skip_ssl_verification,
        )

        def _make_datetimes_timezone_aware(datetime_str: str):
            dt = datetime.fromisoformat(datetime_str)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        repos_to_prs_last_updated = {}
        repos_to_commits_backpopulated_to = {}
        pull_prs_since_for_repo_in_org = {}
        # All date-like objects have to be normalized to timezone-aware datetimes
        # Values passed to the agent are pretty inconsistent (some of them are dates,
        # all of them seem to be datetime agnostic)
        for repo_id, repo_info in endpoint_git_instance_info['repos_dict_v2'].items():
            if repo_info['latest_pr_update_date_pulled']:
                repos_to_prs_last_updated[repo_id] = _make_datetimes_timezone_aware(
                    repo_info['latest_pr_update_date_pulled']
                )
            if repo_info['commits_backpopulated_to']:
                repos_to_commits_backpopulated_to[repo_id] = _make_datetimes_timezone_aware(
                    repo_info['commits_backpopulated_to']
                )
            if repo_info['prs_backpopulated_to']:
                pull_prs_since_for_repo_in_org[repo_id] = _make_datetimes_timezone_aware(
                    repo_info['prs_backpopulated_to']
                )

        pull_from = _make_datetimes_timezone_aware(endpoint_git_instance_info['pull_from'])
        git_configs.append(
            JFIngestGitConfig(
                company_slug=company_slug,
                instance_slug=instance_slug,
                instance_file_key=endpoint_git_instance_info['key'],
                git_provider=agent_git_config.git_provider,
                git_auth_config=jf_ingest_git_auth_config,
                url=agent_git_config.git_url,
                jf_options=jf_options,
                repos_to_prs_last_updated=repos_to_prs_last_updated,
                repos_to_commits_backpopulated_to=repos_to_commits_backpopulated_to,
                repos_to_prs_backpopulated_to=pull_prs_since_for_repo_in_org,
                git_organizations=agent_git_config.git_include_projects,
                pull_from=pull_from,
                excluded_organizations=agent_git_config.git_exclude_projects,
                included_repos=agent_git_config.git_include_repos,
                excluded_repos=agent_git_config.git_exclude_repos,
                included_branches_by_repo=agent_git_config.git_include_branches,
                git_redact_names_and_urls=agent_git_config.git_redact_names_and_urls,
                git_strip_text_content=agent_git_config.git_strip_text_content,
            )
        )

    ingestion_config = IngestionConfig(
        company_slug=company_slug,
        upload_to_s3=config.run_mode_includes_send,
        # NOTE: There is a special run mode for jira where we do not save data locally.
        # This run mode is helpful for very large companies that need to do a large
        # initial upload, and they don't want to provision a massive docker container.
        # TODO: Break this out to optionally apply to only jira or only git
        save_locally=not config.jira_skip_saving_data_locally,
        # TODO: Maybe we set this, although the constructor can handle them being null
        local_file_path=config.outdir,
        timestamp=os.path.split(config.outdir)[1],
        jellyfish_api_token=creds.jellyfish_api_token,
        jellyfish_api_base=config.jellyfish_api_base,
        ingest_type=IngestionType.AGENT,
        jira_config=jira_config,
        git_configs=git_configs,
    )

    return ingestion_config


def _get_git_config(git_config, git_provider_override=None, multiple=False) -> GitConfig:
    git_provider = git_config.get('provider', git_provider_override)
    git_url = git_config.get('url', None)
    git_include_projects = set(git_config.get('include_projects', []))
    git_exclude_projects = set(git_config.get('exclude_projects', []))
    git_include_all_repos_inside_projects = set(
        git_config.get('include_all_repos_inside_projects', [])
    )
    git_exclude_all_repos_inside_projects = set(
        git_config.get('exclude_all_repos_inside_projects', [])
    )
    git_instance_slug = git_config.get('instance_slug', None)
    creds_envvar_prefix = git_config.get('creds_envvar_prefix', None)
    git_include_bbcloud_projects = set(git_config.get('include_bitbucket_cloud_projects', []))
    git_exclude_bbcloud_projects = set(git_config.get('exclude_bitbucket_cloud_projects', []))
    git_include_repos = set(git_config.get('include_repos', []))
    git_exclude_repos = set(git_config.get('exclude_repos', []))
    git_include_branches = dict(git_config.get('include_branches', {}))

    if multiple and not git_instance_slug:
        print('ERROR: Git `instance_slug` is required for multiple Git instance mode.')
        raise BadConfigException()

    if multiple and not creds_envvar_prefix:
        print('ERROR: `creds_envvar_prefix` is required for multiple Git instance mode.')
        raise BadConfigException()

    if git_provider is None:
        print(
            f'ERROR: Should add provider for git configuration. Provider should be one of {git_providers}'
        )
        raise BadConfigException()

    if git_provider not in git_providers:
        print(
            f'ERROR: Unsupported Git provider {git_provider}. Provider should be one of {git_providers}'
        )
        raise BadConfigException()

    # github must be in whitelist mode
    if git_provider == 'github' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: GitHub requires a list of projects (i.e., GitHub organizations) to '
            'pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    if git_provider == 'github' and ('api.github.com' not in git_url and '/api/v3' not in git_url):
        print(f'ERROR: Github enterprise URL appears malformed.  Did you mean "{git_url}/api/v3"?')
        raise BadConfigException()

    # gitlab must be in whitelist mode
    if git_provider == 'gitlab' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: GitLab requires a list of projects (i.e., GitLab top-level groups) '
            'to pull from. Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    # BBCloud must be in whitelist mode
    if git_provider == 'bitbucket_cloud' and (git_exclude_projects or not git_include_projects):
        print(
            'ERROR: Bitbucket Cloud requires a list of projects to pull from.'
            ' Make sure you set `include_projects` and not `exclude_projects`, and try again.'
        )
        raise BadConfigException()

    return GitConfig(
        git_provider=git_provider,
        git_instance_slug=git_instance_slug,
        git_url=git_url,
        git_include_projects=list(git_include_projects),
        git_exclude_projects=list(git_exclude_projects),
        git_include_all_repos_inside_projects=list(git_include_all_repos_inside_projects),
        git_exclude_all_repos_inside_projects=list(git_exclude_all_repos_inside_projects),
        git_include_repos=list(git_include_repos),
        git_exclude_repos=list(git_exclude_repos),
        git_include_branches=dict(git_include_branches),
        git_strip_text_content=git_config.get('strip_text_content', False),
        git_redact_names_and_urls=git_config.get('redact_names_and_urls', False),
        gitlab_per_page_override=git_config.get('gitlab_per_page_override', None),
        git_verbose=git_config.get('verbose', False),
        creds_envvar_prefix=creds_envvar_prefix,
        # legacy fields ===========
        git_include_bbcloud_projects=list(git_include_bbcloud_projects),
        git_exclude_bbcloud_projects=list(git_exclude_bbcloud_projects),
    )
