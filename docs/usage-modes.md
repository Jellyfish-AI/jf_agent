---
title: Usage modes
layout: basic-page
pageDescription: See the different usage modes that the agent offers.
---


The agent has several different usage modes:

* `download_and_send`  
    * Download Jira and/or Git data from your systems; send the resulting data to Jellyfish.  


* `download_only`  
    * Download Jira and/or Git data from your systems. Allow the downloaded data to be inspected before it's sent to Jellyfish.

* `send_only`  
    * Send a previously downloaded dataset to Jellyfish.

* `print_all_jira_fields`  
    * Show the keys and field names for all of your Jira custom fields (to aid in agent configuration).

* `print_apparently_missing_git_repos`  
    * Show the names and urls of Git repositories that may be missing from Jellyfish by looking at the Development Jira custom field (to aid in agent configuration).

* `validate`  
    * Validate the configuration file using APIs.  

Data that you download from Jira and/or Git may be scrubbed to remove sensitive fields and values before you send it to Jellyfish.
