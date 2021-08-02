---
title: Bitbucket Cloud Environment Variable Setup Guide
layout: basic-page-with-footer-links
pageDescription: On this page, you will learn how to add the correct environment variables to your file.
nextPage: Step 3&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Step 2&#58; Create environment variable file
previousPageLink: env-var-setup-guide.html
---


## Details

This is where you'll add and populate your environmental variables file for Bitbucket Cloud.


## Instructions

1. Add the following section to your environment variable file. This is the same file mentioned [in step 2](env-var-setup-guide.html). Adding the following variables allows the agent to access your Bitbucket Cloud data:
    <p class="code-block"><code>
        BITBUCKET_CLOUD_USERNAME=...<br/>
        BITBUCKET_CLOUD_APP_PASSWORD=...
    </code></p>

2. Use your Bitbucket Cloud username as the value for `BITBUCKET_CLOUD_USERNAME`

3. Get the value for `BITBUCKET_CLOUD_APP_PASSWORD`. Create an app password in Bitbucket, following the instructions [here](https://support.atlassian.com/bitbucket-cloud/docs/app-passwords/#Apppasswords-Createanapppassword).

4. If you have additional systems not yet configured in your environment variable file, refer back to [step 2](env-var-setup-guide.html) to set up your other systems.
