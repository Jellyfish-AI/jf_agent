---
title: Jira Setup
---

1. Add the following section to your environment variable file. This is the same file mentioned in step 3 above. Adding the following variables allows the agent to access your Jira data:
    <p class="code-block"><code>
        JIRA_USERNAME=...<br/>
        JIRA_PASSWORD=...
    </code></p>

2. Get a value for `JIRA_USERNAME`. Choose a Jira username that has read access to all of the projects you would like to use in Jellyfish.

3. The value for `JIRA_PASSWORD` will vary. If you are using Jira server, enter the password for the user specified with `JIRA_USERNAME`. If you are using Jira Cloud, create a personal API token, following the instructions [here](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/).

4. Populate the appropriate values for your Jira configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L13-L111) section of the yml file. Follow the instructions provided in the yml file.
