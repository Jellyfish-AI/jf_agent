---
title: Keeping the agent updated
layout: basic-page-with-footer-links
pageDescription: Describes how to keep the JF agent updated.
nextPage: Specify usage mode
nextPageLink: specify-usage.html
previousPage: Run the agent
previousPageLink: run-agent.html
---

You can pull down the latest Docker image from Docker Hub with:
    
<p class="code-block"><code>
    docker pull jellyfishco/jf_agent:stable
</code></p>  

You may also want to periodically perform that `docker pull` command, or prepend it to the command you use for `docker run`, to ensure you're using the latest version of the agent.

