---
title: Run the agent
layout: basic-page-with-footer-links
pageDescription: Describes how to run the agent.
nextPage: Keep the agent updated
nextPageLink: agent-updated.html
previousPage: Step 3&#58; Ensure proper network configuration
previousPageLink: network-config-setup-guide.html
---

The agent is distributed as a Docker image. The image bundles the agent's source code, a Python 3 environment, and the AWS command line tools.  

Execute the agent with a `docker run` command that references the image on Docker Hub. You'll use bind mounts and environment variables to configure it with your YAML file and credentials.

* The YAML configuration file you've created should be provided to the container via a bind mount. The syntax for providing a bind mount is:
    <p class="code-block"><code>
        --mount type=bind,source=&lt;host_path&gt;,target=&lt;container_path&gt;
    </code></p>
    The `host_path` should be the full path to where you've stored the YAML configuration file. The `container_path` must be `/home/jf_agent/config.yml`.  

* Your credentials should be provided to the container via environment variables. The syntax for providing environment variables from a file is:
    <p class="code-block"><code>
        --env-file &lt;full_path_to_env_file&gt;
    </code></p>

## Sample run commands

Below are some common use cases for running the agent, and their corresponding `docker run` commands.  

**Please be sure to update the commands with the proper paths when copying them!**

* Normal mode: download and send
    <p class="code-block"><code>
        docker pull jellyfishco/jf_agent:stable && \<br/>
        docker run --rm \<br/>
        --mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \<br/>
        --env-file /full/path/creds.env \<br/>
        jellyfishco/jf_agent:stable<br/>
    </code></p>  

* Download data without sending
    <p class="code-block"><code>
        docker pull jellyfishco/jf_agent:stable && docker run --rm \<br/>
        --mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \<br/>
        --mount type=bind,source=/full/path/jf_agent_output,target=/home/jf_agent/output \<br/>
        --env-file ./creds.env \<br/>
        jellyfishco/jf_agent:stable -m download_only<br/>
    </code></p>

* Send previously downloaded data
    <p class="code-block"><code>
        docker pull jellyfishco/jf_agent:stable && docker run --rm \<br/>
        --mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \<br/>
        --mount type=bind,source=/full/path/jf_agent_output,target=/home/jf_agent/output \<br/>
        --env-file ./creds.env \<br/>
        jellyfishco/jf_agent:stable -m send_only -od ./output/20190822_133513<br/>
    </code></p>

* Print info on Jira fields
    <p class="code-block"><code>
        docker pull jellyfishco/jf_agent:stable && docker run --rm \<br/>
        --mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \<br/>
        --env-file ./creds.env \<br/>
        jellyfishco/jf_agent:stable -m print_all_jira_fields<br/>
    </code></p>

* Print Git repos apparently missing from Jellyfish
    <p class="code-block"><code>
        docker pull jellyfishco/jf_agent:stable && docker run --rm \<br/>
        --mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \<br/>
        --env-file ./creds.env \<br/>
        jellyfishco/jf_agent:stable -m print_apparently_missing_git_repos<br/>
    </code></p>

* Validate configuration
    <p class="code-block"><code>
        docker pull jellyfishco/jf_agent:stable && docker run --rm \<br/>
        --mount type=bind,source=/full/path/ourconfig.yml,target=/home/jf_agent/config.yml \<br/>
        --env-file ./creds.env \<br/>
        jellyfishco/jf_agent:stable -m validate<br/>
    </code></p>


## Additional execution guides

For details on more ways to run your agent, refer to the links below.

* [Keep the agent updated](agent-updated.html)
* [Specify a usage mode](specify-usage.html)
* [Save downloaded output](save-download.html)
* [Send a downloaded dataset](send-dataset.html)
