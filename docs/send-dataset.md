---
title: Send a downloaded dataset
layout: basic-page-with-footer-links
pageDescription: Describes how to specify a previously downloaded dataset to be sent to Jellyfish.
nextPageStyle: "display: none"
previousPage: Save downloaded output
previousPageLink: save-download.html
---

If you've run the agent in `download_only` mode so that you can inspect its output, when you're ready to send the data to Jellyfish you'll use the `send_only` mode. You'll provide a bind mount for the output directory, and you'll also provide the `-od` argument to specify a path relative to the container's output directory that contains the data previously downloaded.  

When the agent runs, it saves its downloaded data in a timestamped directory inside of `/home/jf_agent/output`. It shows the directory its downloaded data is being written to with a line like this:
<p class="code-block"><code>
    Will write output files into ./output/20190822_133513
</code></p>  

So, e.g., if an earlier run with `download_only` may have written its output file into `./output/20190822_133513` and the host directory `/tmp/jf_agent/output` had been mounted at `/home/jf_agent/output`, you'd use these arguments to send that data to Jellyfish:
<p class="code-block"><code>
    --mount type=bind,source=/tmp/jf_agent_output,target=/home/jf_agent/output
    -m send_only
    -od ./output/20190822_133513
</code></p>

