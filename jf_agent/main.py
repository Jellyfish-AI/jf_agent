import argparse
import os
import sys
import json

from jira import JIRA
from jira.resources import GreenHopperResource

from jf_agent.jira_download import download_users, download_fields, download_resolutions, download_issuetypes, \
    download_issuelinktypes, download_priorities, download_projects_and_versions, download_boards_and_sprints, \
    download_issues, download_worklogs

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('jira_url', help='the fully-qualified base URL for your Jira server, e.g. https://jira.yourcompany.com')
    parser.add_argument('-o', '--out_dir', nargs='?', default='.', help='output directory')
    args = parser.parse_args()
    
    jira_username = os.environ.get('JIRA_USERNAME', None)
    jira_password = os.environ.get('JIRA_PASSWORD', None)

    jira_url = args.jira_url
    outdir = args.out_dir
    
    if jira_username and jira_password:
        jira_connection = get_basic_jira_connection(jira_url, jira_username, jira_password)
    else:
        print('Jira credentials not found. Set environment variables, either JIRA_SHARED_SECRET, JIRA_CLIENT_KEY, JIRA_KEY (for OAuth) or JIRA_USERNAME and JIRA_PASSWORD (for basic auth)')
        parser.print_help()
        sys.exit(1)
    
    write_file(outdir, 'users', download_users(jira_connection))
    write_file(outdir, 'fields', download_fields(jira_connection))
    write_file(outdir, 'resolutions', download_resolutions(jira_connection))
    write_file(outdir, 'issuetypes', download_issuetypes(jira_connection))
    write_file(outdir, 'linktypes', download_issuelinktypes(jira_connection))
    write_file(outdir, 'priorities', download_priorities(jira_connection))
    write_file(outdir, 'projects_and_versions', download_projects_and_versions(jira_connection))

    boards, sprints, links = download_boards_and_sprints(jira_connection)
    write_file(outdir, 'boards', boards)
    write_file(outdir, 'sprints', sprints)
    write_file(outdir, 'board_sprint_links', links)
    
    write_file(outdir, 'issues', download_issues(jira_connection))
    write_file(outdir, 'worklogs',download_worklogs(jira_connection))
    

def get_basic_jira_connection(url, username, password):
    return JIRA(
        server=url,
        basic_auth=(username, password),
        max_retries=10,
        options={
            'agile_rest_path': GreenHopperResource.AGILE_BASE_REST_PATH
        }
    )


def write_file(outdir, name, jira_results):
    with open(f'{outdir}/{name}.json', 'w') as outfile:
        json.dump(jira_results, outfile, indent=2)

