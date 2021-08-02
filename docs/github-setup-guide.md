---
title: GitHub Environment Variable Setup Guide
layout: basic-page-with-footer-links
pageDescription: On this page, you will learn how to add the correct environment variables to your file.
nextPage: Step 3&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Step 2&#58; Create environment variable file
previousPageLink: env-var-setup-guide.html
---


## Details

This is where you'll add and populate your environmental variables file for GitHub.


## Instructions

1. Add the following section to your environment variable file. This is the same file mentioned [in step 2](env-var-setup-guide.html). Adding the following variables allows the agent to access your GitHub data:
    <p class="code-block"><code>
        GITHUB_TOKEN=...
    </code></p>

2. Create a personal access token in GitHub, following the instructions [here](https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/creating-a-personal-access-token). Use this token as the value for `GITHUB_TOKEN`.

3. If you have additional systems not yet configured in your environment variable file, refer back to [step 2](env-var-setup-guide.html) to set up your other systems.
