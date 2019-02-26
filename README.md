# jf_agent

An agent that can run on-premise and collect data to be sent to [Jellyfish](https://jellyfish.co).

## Usage

1. Install jf_agent with `pip`:
```bash
pip install jf_agent
```
2. For Jira: Gather your Jira credentials. You'll need a Jira username with read access to the right projects, along with an API token for that user.

3. Set up environment variables with your Jira credentials. Set JIRA_USERNAME and JIRA_PASSWORD to the username and API token you found above.

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

  # optional: only pull issues from specific projects.  Comment this out
  # to pull issues from all projects.
  project_whitelist:
    - PROJ1
    - PROJ2

bitbucket:
  # URL to bitbucket
  url: https://bitbucket.yourcompany.com
```

5. Run `jf_agent` with the path to your config file:
```
jf_agent -c jellyfish.yaml
```

6. Collect the generated files from the output directory you specified, and send them to Jellyfish.
