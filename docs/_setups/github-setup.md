---
title: GitHub Setup
---

1. Add the following section to your environment variable file. This is the same file mentioned in step 3 above. Adding the following variables allows the agent to access your GitHub data:
    <p class="code-block"><code>
        ORG1_GITHUB_TOKEN=...
    </code></p>

2. Create a personal access token in GitHub, following the instructions [here](https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/creating-a-personal-access-token). Use this token as the value for `ORG1_GITHUB_TOKEN`.

3. Populate the appropriate values for your Git configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L114-L211) section of the yml file. Follow the instructions provided in the yml file.
