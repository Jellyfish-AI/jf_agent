---
title: GitLab Setup
---

1. Add the following section to your environment variable file. This is the same file mentioned in step 3 above. Adding the following variables allows the agent to access your GitLab data:
    <p class="code-block"><code>
        ORG1_GITLAB_TOKEN=...
    </code></p>

2. Create a personal access token in GitLab, following the instructions [here](https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html#creating-a-personal-access-token). Use this token as the value for `ORG1_GITLAB_TOKEN`.

3. Populate the appropriate values for your Git configuration in the `example.yml` file you copied above from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L114-L211) section of the yml file. Follow the instructions provided in the yml file.
