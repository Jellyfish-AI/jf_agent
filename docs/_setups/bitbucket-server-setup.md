---
title: Bitbucket Server Setup
---

1. Add the following section to your environment variable file. This is the same file mentioned in step 3 above. Adding the following variables allows the agent to access your Bitbucket Server data:
    <p class="code-block"><code>
        ORG1_BITBUCKET_USERNAME=...<br/>
        ORG1_BITBUCKET_PASSWORD=...
    </code></p>

2. `ORG1_BITBUCKET_USERNAME` should be your Bitbucket server's username

3. `ORG1_BITBUCKET_PASSWORD` should be your Bitbucket server's password

4. Populate the appropriate values for your Git configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L114-L211) section of the yml file. Follow the instructions provided in the yml file.
