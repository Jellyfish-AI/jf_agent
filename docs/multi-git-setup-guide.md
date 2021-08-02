---
title: Multiple Git Instance Environment Variable Setup Guide
layout: basic-page-with-footer-links
pageDescription: On this page, you will find a step-by-step guide on how to setup the JF agent to work with multiple Git instances.
nextPage: Step 3&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Step 2&#58; Create environment variable file
previousPageLink: env-var-setup-guide.html
---


## Details

This is where you'll add and populate your environmental variables file for multiple Git instances


## Instructions

1. You will want to add environment variables for each Git instance you have, to configure your environment variable file. This is the same file mentioned [in step 2](env-var-setup-guide.html). Adding the variables allows the agent to access your Git data.  

    With multiple git instances, all git credentials must be prefixed with the corresponding `creds_envvar_prefix` provided in the `config.yml`.  

    For example, if the `creds_envvar_prefix` is set to `ORG1` for a Bitbucket instance, the configuration would include the following variables:
    <p class="code-block"><code>
        ORG1_BITBUCKET_USERNAME=...<br/>
        ORG1_BITBUCKET_PASSWORD=...
    </code></p>  
    
    When in multi-git mode, `creds_envvar_prefix` is required for all git instances. See the `example.yml` from [Step 1](yaml-config-setup-guide.html) of this setup guide for more details.

2. For each of your Git instances, add the appropriate environment variables from the choices below.
    * [GitHub Environment Variables Setup Guide](github-setup-guide.html)
    * [GitLab Environment Variables Setup Guide](gitlab-setup-guide.html)
    * [Bitbucket Server Environment Variables Setup Guide](bitbucket-server-setup-guide.html)
    * [Bitbucket Cloud Environment Variables Setup Guide](bitbucket-cloud-setup-guide.html)  


4. If you have additional systems, such as Jira, not yet configured in your environment variable file, refer back to [step 2](env-var-setup-guide.html) to set up your other systems.
