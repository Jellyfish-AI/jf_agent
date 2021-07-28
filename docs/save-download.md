---
title: Save downloaded output
layout: basic-page-with-footer-links
pageDescription: Describes how to save the output that the agent has downloaded.
nextPage: Send a downloaded dataset
nextPageLink: send-dataset.html
previousPage: Specify usage mode
previousPageLink: specify-usage.html
---

By default, the agent will download and send the data it collects. Upon completion the data downloaded will be stored inside the container. If you use the `--rm` argument to `docker run` then the container and the data will be cleaned up when the agent completes.  

If you instead want to save the downloaded output (perhaps so that you can inspect it), you can provide a bind mount that maps a host directory to the container's agent output directory.  

Just like for providing the YAML configuration file, the syntax for providing a bind mount for the agent output directory is:

<p class="code-block"><code>
    --mount type=bind,source=&lt;host_path&gt;,target=&lt;container_path&gt;
</code></p>  
    
In this case, the `host_path` should be the full path to a directory on the host and the `container_path` must be `/home/jf_agent/output`.

