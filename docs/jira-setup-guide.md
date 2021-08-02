---
title: Jira Environment Variable Setup Guide
layout: basic-page-with-footer-links
pageDescription: On this page, you will find a step-by-step guide on how to setup the JF agent to work with Jira.
nextPage: Step 3&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Step 2&#58; Create environment variable file
previousPageLink: env-var-setup-guide.html
---


## Details

This is where you'll add and populate your environmental variables file for Jira.  If you want your Git instance to work with the agent, see the setup guide corresponding to the type of Git instance you have - it will include Jira instructions as well.


## Instructions

1. Add the following section to your environment variable file. This is the same file mentioned [in step 2](env-var-setup-guide.html). Adding the following variables allows the agent to access your Jira data:
    <p class="code-block"><code>
        JIRA_USERNAME=...<br/>
        JIRA_PASSWORD=...
    </code></p>

2. Get a value for `JIRA_USERNAME`. Choose a Jira username that has read access to all of the projects you would like to use in Jellyfish.

3. The value for `JIRA_PASSWORD` will vary. If you are using Jira server, enter the password for the user specified with `JIRA_USERNAME`. If you are using Jira Cloud, create a personal API token, following the instructions [here](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/).

4. If you have additional systems not yet configured in your environment variable file, refer back to [step 2](env-var-setup-guide.html) to set up your other systems.
