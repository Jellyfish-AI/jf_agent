---
title: Bitbucket Cloud Setup
---

1. Add the following section to your environment variable file. This is the same file mentioned in step 3 above. Adding the following variables allows the agent to access your Bitbucket Cloud data:
    <p class="code-block"><code>
        ORG1_BITBUCKET_CLOUD_USERNAME=...<br/>
        ORG1_BITBUCKET_CLOUD_APP_PASSWORD=...
    </code></p>

2. Use your Bitbucket Cloud username as the value for `ORG1_BITBUCKET_CLOUD_USERNAME`

3. Get the value for `ORG1_BITBUCKET_CLOUD_APP_PASSWORD`. Create an app password in Bitbucket, following the instructions [here](https://support.atlassian.com/bitbucket-cloud/docs/app-passwords/#Apppasswords-Createanapppassword).

4. Populate the appropriate values for your Git configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L114-L211) section of the yml file. Follow the instructions provided in the yml file.
