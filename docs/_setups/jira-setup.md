---
title: Jira Setup
---

Before you begin, determine which credential setup you will need to use. Most of the time, you will want to set up credentials using basic auth. If you are running Jira server (i.e. not Jira cloud) and have [disabled basic authentication](https://confluence.atlassian.com/enterprise/disabling-basic-authentication-1044776464.html), you will want to use bearer token auth. If you aren't sure, use basic auth.

## Basic Auth

1. Add the following section to your environment variable file. This is the same file mentioned in step 3 above. Adding the following variables allows the agent to access your Jira data:
    <p class="code-block"><code>
        JIRA_USERNAME=...<br/>
        JIRA_PASSWORD=...
    </code></p>

2. Get a value for `JIRA_USERNAME`. Choose a Jira username that has read access to all of the projects you would like to use in Jellyfish.

3. The value for `JIRA_PASSWORD` will vary. If you are using Jira server, enter the password for the user specified with `JIRA_USERNAME`. If you are using Jira Cloud, create a personal API token, following the instructions [here](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/).

4. Populate the appropriate values for your Jira configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L13-L111) section of the yml file. Follow the instructions provided in the yml file.

## Bearer Token Auth

1. Create a user in your Jira server that has read access to all projects you would like the agent to ingest. If you have already created such a user, open a browser while logged in as said user.

2. Retrieve a Personal Access Token for the user from step 2 following [this guide](https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html).

3. Add the Personal Access Token from the previous step to the environment variable file. This is the same file mentioned in step 3 above. This will allow the agent to access your Jira data. The variable to which this token should be assigned will differ depending upon whether you are using Jira Server or Jira Cloud<br/>

    **Jira Server**:<br/>
    <p class="code-block"><code>
    JIRA_BEARER_TOKEN=...<br/>
    </code></p>

    **Jira Cloud**:<br/>
    <p class="code-block"><code>
    JIRA_PASSWORD=...<br/>
    </code></p>

4. Populate the appropriate values for your Jira configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L13-L111) section of the yml file. Follow the instructions provided in the yml file.
