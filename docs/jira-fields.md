---
title: Specify Jira Fields
layout: basic-page
pageDescription: Choose to only allow the agent to access certain fields in Jira.
---


It is possible to configure the agent to pull a subset of fields from Jira.  This can be useful if, for example, certain fields contain sensitive data that you don't want to send to Jellyfish.  

This can be controlled through the `include_fields` and `exclude_fields` options in the config file.  Note, however, that certain fields are required in order for Jellyfish to work. These required fields are:  
<p class="code-block"><code>
    issuekey<br/>
    parent<br/>
    issuelinks<br/>
    project<br/>
    reporter<br/>
    assignee<br/>
    creator<br/>
    issuetype<br/>
    resolution<br/>
    resolutiondate<br/>
    status<br/>
    created<br/>
    updated<br/>
    subtasks<br/>
</code></p>

Some of the Jira agile feature are built internally on "custom fields" that Jellyfish uses. These custom fields have keys in the form `customfield_XXXXX`, but where the digits represented by X are different in each Jira installation. You can find the keys for your custom fields by running the agent in the `print_all_jira_fields` mode.  The custom fields used by Jellyfish are the following:
<p class="code-block"><code>
    Epic Link<br/>
    Epic Name<br/>
    Sprint<br/>
    Parent Link<br/>
    Story Points<br/>
    Rank<br/>
</code></p>

Make sure that at least these fields are configured for Jellyfish to pull.  

Note that the `print_apparently_missing_git_repos` mode requires that Jellyfish have access to the Development custom field and that we have already processed your data with this field included.  

Additional Jellyfish functionality is enabled if the following fields are pulled:
<p class="code-block"><code>
    summary<br/>
    description<br/>
    priority<br/>
    worklog<br/>
    comment<br/>
    timetracking<br/>
    duedate<br/>
    labels<br/>
    fixVersions<br/>
    versions<br/>
    components<br/>
    timeestimate<br/>
    timeoriginalestimate<br/>
    timespent<br/>
    aggregatetimespent<br/>
    aggregatetimeoriginalestimate<br/>
    aggregatetimeestimate<br/>
</code></p>
