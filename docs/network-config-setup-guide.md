---
title: Step 3&#58; Ensure proper network configuration
layout: basic-page-with-footer-links
pageDescription: On this page, you will find instructions on how to make sure your network configuration is set up properly.
nextPage: How to run the agent
nextPageLink: run-agent.html
previousPage: Step 2&#58; Create environment variable file
previousPageLink: env-var-setup-guide.html
---

## Details

This step helps make sure your network is set up to work with the Jellyfish Agent. This is the final step in the agent setup guide!


## Instructions

The agent will need to make various requests to function. Thus, we ask that you please ensure that your network firewall/proxies are configured such that the agent is able to:

1. Make requests to the Jellyfish API at https://app.jellyfish.co:443/

2. Make requests to your Jira and Git host(s) on port 443

3. Make requests to URLs under s3.amazonaws.com on port 443
