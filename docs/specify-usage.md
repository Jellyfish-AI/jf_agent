---
title: Specify usage mode
layout: basic-page-with-footer-links
pageDescription: Describes how to specify a usage mode to the agent.
nextPage: Save downloaded output
nextPageLink: save-download.html
previousPage: Keep the agent updated
previousPageLink: agent-updated.html
---

The usage mode is provided to the agent via the `-m` argument. The value should be one of: `download_and_send`, `download_only`, `send_only`, `print_all_jira_fields`, `print_apparently_missing_git_repos`, or `validate`.  

If you don't provide a `-m` argument, the `download_and_send` mode is used.  

You can see more details about the various usage modes [here](usage-modes.html).
