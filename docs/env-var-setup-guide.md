---
title: Step 2&#58; Create environment variable file
layout: basic-page-with-footer-links
pageDescription: On this page, you will find instructions on how to set up your environment variables, to store authentication information.
nextPage: Step 3&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Step 1&#58; Create YAML configuration file
previousPageLink: yaml-config-setup-guide.html
---

## Details

This is where you'll store authentication information, such as your API tokens and login credentials.


## Instructions

1. Create an empty file

2. Start by adding the following code snippet to the empty file, replacing the ellipses with the value for `JELLYFISH_API_TOKEN` that you got from Jellyfish:  

    <p class="code-block"><code>
        JELLYFISH_API_TOKEN=...
    </code></p>

3. The way the rest of the file looks will vary based on your organization's toolset. Choose the options below that match your organization's system to see what environment variables are required. Depending on your setup, you may need to add the variables from more than one of these links.  

    If you want to use Jellyfish with multiple instances of Git, please refer to the [Multiple Git Instances Environment Variables Setup Guide](multi-git-setup-guide.html) first before proceeding to your Git instances' specific setup guide.

    * [Jira Environment Variables Setup Guide](jira-setup-guide.html)
    * [Multiple Git Instances Environment Variables Setup Guide](multi-git-setup-guide.html)
    * [GitHub Environment Variables Setup Guide](github-setup-guide.html)
    * [GitLab Environment Variables Setup Guide](gitlab-setup-guide.html)
    * [Bitbucket Server Environment Variables Setup Guide](bitbucket-server-setup-guide.html)
    * [Bitbucket Cloud Environment Variables Setup Guide](bitbucket-cloud-setup-guide.html)