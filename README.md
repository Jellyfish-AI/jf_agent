# jf_agent

An agent that can run on-premise and collect data to be sent to [Jellyfish](https://jellyfish.co/).

## Usage

1. In a python 3 environment, install jf_agent with `pip`:
```bash
pip install jf_agent
```

or, depending how your python environment is set up,

```bash
pip3 install jf_agent
```

2. For Jira: Gather your Jira credentials. You'll need a Jira username with read access to the right projects, along with the password for that user.

3. Set up environment variables with your Jira credentials. Set `JIRA_USERNAME` and `JIRA_PASSWORD` to the username and password you found above.

4. For Bitbucket: Gather your Bitbucket credentials. Set the `BITBUCKET_USERNAME` and `BITBUCKET_PASSWORD` environment variables to appropriate values.

5. Create a YAML config file that tell jf_agent how to run. An example config file could be:

```
global:
  # Location to put output files
  out_dir: /tmp/agent

  # Set this to True to skip verification of server SSL certificates.  This might
  # be useful if your Jira / Bitbucket server doesn't have a valid SSL certificate.
  no_verify_ssl: False

jira:
  # URL to jira
  url: https://jira.yourcompany.com

  # Uncomment this to print the list of available fields and exit
  # print_fields_only: True

  # GDPR mode: enable this if your Jira instance's API has User
  # endpoints modified in order to support GDPR.  This should be True
  # for Atlassian Cloud hosted JIRA as of March 30, 2019.
  gdpr_active: False
  
  # only pull issues from specific projects.  Comment this out
  # to pull issues from all projects.
  include_projects:
    - PROJ1
    - PROJ2

  # Uncomment this to pull issues from all but specific projects.
  # exclude_projects:
  #   - PROJ1

  # Uncomment to pull issues from specific project categories only.
  # include_project_categories:
  #   - Engineering

  # Uncomment this to pull issues from all but specific project categories.
  # exclude_project_categories:
  #   - Support

  # Uncomment this to pull only specific fields on issues.
  # include_fields:
  #   - id
  #   - summary

  # Uncomment this to pull all but specific fields on issues.
  # exclude_fields:
  #   - description
  #   - comment


git:
  # supported providers are `bitbucket_server` and `github`
  provider: bitbucket_server
  
  # URL to bitbucket or github.  For github cloud, this should be https://api.github.com; otherwise, use the URL to your local git server.
  url: https://bitbucket.yourcompany.com
  
  # only pull from specific projects / organizations.  Required for github; comment this out to pull from all projects for bitbucket server.
  include_projects:
      - PROJ1
      
  # Uncomment this to pull from all but specific projects (not supported for github).
  #  exclude_projects:
  #    - PROJ1
      
  # only pull from specific repos.  Comment this out to pull from all repos.
  include_repos:
      - my_repository
      
  # Uncomment this to pull from all but specific repos.
  # exclude_repos:
  #    - repo_to_skip
      
  # Strip out long-form text content (commit messages, PR text, etc)
  strip_text_content: False
  
  # Redact names and URLs for projects, repos, branches
  redact_names_and_urls: False
```

5. Run `jf_agent` with the path to your config file, optionally specifying constraints on time to pull git data:
```
jf_agent -c jellyfish.yml [--since 2018-01-01] [--until 2019-04-02]
```

6. Collect the generated files from the output directory you specified, and send them to Jellyfish.


## Fields

It is possible to configure the agent to pull a subset of fields from
Jira.  This can be useful if, for example, certain fields contain
sensitive data that you don't want to send to Jellyfish.

This can be controlled through the `include_fields` and `exclude_fields`
options in the config file.  Note, however, that certain fields are required in order
for Jellyfish to work.  These required fields are:

```
issuekey
project
reporter
assignee
creator
issuetype
resolution
resolutiondate
status
created
updated
subtasks
```

Some of the Jira agile feature are built internally on "custom fields" that Jellyfish uses. These
custom fields have keys in the form `customfield_XXXXX`, but where the digits represented by X
are different in each Jira installation. You can find the keys for your custom
fields by running the agent with the `print_fields_only` option in the config file.  The custom
fields used by Jellyfish are the following:

```
Epic Link
Epic Name
Sprint
Parent Link
Story Points
Rank
```

Make sure that at least these fields are configured for Jellyfish to pull.

Additional Jellyfish functionality is enabled if the following fields are pulled:
```
summary
description
priority
worklog
comment
timetracking
duedate
labels
fixVersions
versions
components
timeestimate
timeoriginalestimate
timespent
aggregatetimespent
aggregatetimeoriginalestimate
aggregatetimeestimate
```
