---
title: Step 1&#58; Configure the Agent
layout: basic-page-with-footer-links
pageDescription: Describes how to configure the agent to your systems.
nextPage: Step 2&#58; Ensure proper network configuration
nextPageLink: network-config-setup-guide.html
previousPage: Setting up the agent
previousPageLink: setup-guide.html
---

{% comment %}

    PLEASE READ!!!
    --------------

    If you have come to edit the individual setup guides, please note that each accordion section is its own file, which are located in the `_setups` folder.

{% endcomment %}


## Details

This will help the agent work with your systems! Using a sample YAML file, along with a file to configure environment variables, we'll ask you to put in some details for your systems, and specify the customization options you'd like.


## Instructions

1. Copy this [example YAML configuration file](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml).

2. Optionally, alter the global configuration options you would like from the `example.yml` file from step 1. This is [this](https://github.com/Jellyfish-AI/jf_agent/blob/master/example.yml#L1-L11) section of the yml file.

3. Create an additional, empty, file for your environment variables

4. Add the following code snippet to the empty file from step 3, replacing the ellipses with the value for `JELLYFISH_API_TOKEN` that you got from Jellyfish:  

    <p class="code-block"><code>
        JELLYFISH_API_TOKEN=...
    </code></p>

5. The way the rest of the files look will vary based on your organization's toolset. Choose the options below that match your organization's system to see what changes are required. Depending on your setup, you may need to follow instructions from multiple different sections.

    **Make sure that for each of your Git environment variables**, they have a prefix that corresponds to `creds_envvar_prefix` provided in the `example.yml` from step 1.

    For example, if the `creds_envvar_prefix` is set to `ORG1` for a Bitbucket instance, the configuration would include the following variables:
    <p class="code-block"><code>
        ORG1_BITBUCKET_USERNAME=...<br/>
        ORG1_BITBUCKET_PASSWORD=...
    </code></p>  

    For each of the following examples, we will be using `ORG1` as our `creds_envvar_prefix`.

<ul class="jekyllcodex_accordion">
    {% for item in site.setups %}
        <li class="accitem"><input id="accordion{{ forloop.index }}" type="checkbox" /><label for="accordion{{ forloop.index }}">{{ item.title }}</label><div>{{ item.content }}</div></li>
    {% endfor %}
</ul>