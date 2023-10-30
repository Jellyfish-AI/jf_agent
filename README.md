# jf_agent

An agent that can run on-premise to download and send data to [Jellyfish](https://jellyfish.co/).

See the documentation [here](https://jf-public.s3.amazonaws.com/Jellyfish+Agent+Guide.pdf)

## Using the agent with custom SSL/TLS certificates

Some organizations have generated their own certificates, usually via an organization-wide Certificate Authority (CA) certificate. If the certificate chain for a system that the jf_agent connects to does not contain a certificate that's known to the agent, the agent will terminate with an error like:

```text
[2101] Failed to connect to bitbucket_server:
HTTPSConnectionPool(host='bitbucket.example.com', port=443): Max retries exceeded with url: /rest/
(Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1129)')))
```

Here's how to fix it:

1. Download a recent certificate bundle from <https://github.com/certifi/python-certifi> such as [`certifi/cacert.pem` as of the `2021.10.08` tag](https://github.com/certifi/python-certifi/blob/2021.10.08/certifi/cacert.pem). ([certifi](https://pypi.org/project/certifi/) is a dependency of [requests](https://pypi.org/project/requests/) and its `cacert.pem` bundle can be found inside the container)
2. Obtain the entire _chain_ of certificates that the servers are using, in Base-64 encoded X.509 (PEM) format.
3. Append the certificates obtained in the second step to the `cacert.pem` file obtained in the first step.
4. The last piece of the puzzle is to make the updated `cacert.pem` bundle accessible to the agent and to point the `REQUESTS_CA_BUNDLE` environment variable to it.  For example, here is what a Bash script could look like when it's invoking `docker run` (notice how we're mounting the local `cacert.pem` file to a path inside the container, and then setting the `REQUESTS_CA_BUNDLE` environment variable in the container to point to it):

    ```bash
    HERE=$(pwd)
    PATH_TO_BUNDLE=/home/jf_agent/cacert.pem
    OUTPUT_FOLDER=${HERE}/jf_agent_output
    mkdir --parents ${OUTPUT_FOLDER}
    docker run -it --rm \
        --mount type=bind,source=${HERE}/my_config.yml,target=/home/jf_agent/config.yml \
        --mount type=bind,source=${OUTPUT_FOLDER},target=/home/jf_agent/output \
        --mount type=bind,source=${HERE}/cacert.pem,target=${PATH_TO_BUNDLE} \
        --env REQUESTS_CA_BUNDLE=${PATH_TO_BUNDLE} \
        --env-file ./creds.env \
        jellyfishco/jf_agent:stable \
        $@
    ```
