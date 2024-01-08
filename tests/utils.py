import requests_mock

from jf_agent.jf_jira import get_basic_jira_connection


def get_connection():
    mock_server_info_resp = (
        '{"baseUrl":"https://test-co.atlassian.net","version":"1001.0.0-SNAPSHOT",'
        '"versionNumbers":[1001,0,0],"deploymentType":"Cloud","buildNumber":100218,'
        '"buildDate":"2023-03-16T08:21:48.000-0400","serverTime":"2023-03-17T16:32:45.255-0400",'
        '"scmInfo":"9999999999999999999999999999999999999999","serverTitle":"JIRA",'
        '"defaultLocale":{"locale":"en_US"}} '
    )
    mock_field_resp = (
        '[{"id":"statuscategorychangedate","key":"statuscategorychangedate","name":"Status Category '
        'Changed","custom":false,"orderable":false,"navigable":true,"searchable":true,"clauseNames":['
        '"statusCategoryChangedDate"],"schema":{"type":"datetime",'
        '"system":"statuscategorychangedate"}}] '
    )

    class PartialConfig:
        jira_url = 'https://test-co.atlassian.net/'
        skip_ssl_verification = False

    class PartialCreds:
        jira_password = None
        jira_username = None
        jira_bearer_token = 'asdf'

    config = PartialConfig()
    creds = PartialCreds()
    # you can test behavior against a live jira instance by setting an email as `jira_username`
    # and storing a generated token in env and retrieving like so:
    # creds.jira_bearer_token = os.environ.get('JIRA_TOKEN')

    # https://test-co.atlassian.net/rest/api/2/serverInfo
    with requests_mock.Mocker() as m:
        m.register_uri(
            'GET',
            'https://test-co.atlassian.net/rest/api/2/serverInfo',
            text=f'{mock_server_info_resp}',
        )
        m.register_uri(
            'GET', 'https://test-co.atlassian.net/rest/api/2/field', text=f'{mock_field_resp}'
        )
        jira_conn = get_basic_jira_connection(config, creds)

    return jira_conn
