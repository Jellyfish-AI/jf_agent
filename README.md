# jf_agent

An agent that can run on-premise and collect data to be sent to [Jellyfish](https://jellyfish.co).

## Usage

1. Install jf_agent with `pip`:
```bash
pip install jf_agent
```
2. Gather your Jira credentials. You'll need a Jira username with read access to the right projects, along with an API token for that user.

3. Set up environment variables with your Jira credentials. Set JIRA_USERNAME and JIRA_PASSWORD to the username and API token you found above.

4. Run `jf_agent`:
```
jf_agent -o <output_directory> <jira_URL>
```

5. Collect the generated files from the output directory you specified, and send them to Jellyfish.
