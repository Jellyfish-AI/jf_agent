---
title: Bitbucket Server Environment Variable Setup Guide
layout: basic-page-with-footer-links
pageDescription: On this page, you will learn how to add the correct environment variables to your file.
nextPage: Step 3&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Step 2&#58; Create environment variable file
previousPageLink: env-var-setup-guide.html
---


## Details

This is where you'll add and populate your environmental variables file for Bitbucket Server.


## Instructions

1. Add the following section to your environment variable file. This is the same file mentioned [in step 2](env-var-setup-guide.html). Adding the following variables allows the agent to access your Bitbucket Server data:
    <p class="code-block"><code>
        BITBUCKET_USERNAME=...<br/>
        BITBUCKET_PASSWORD=...
    </code></p>

2. `BITBUCKET_USERNAME` should be your Bitbucket server's username

3. `BITBUCKET_PASSWORD` should be your Bitbucket server's password

4. If you have additional systems not yet configured in your environment variable file, refer back to [step 2](env-var-setup-guide.html) to set up your other systems.

