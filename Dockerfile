FROM python:3.10.12 AS py-deps

RUN pip install -U pip setuptools wheel
RUN pip install pdm


WORKDIR /python
COPY pyproject.toml pdm.lock README.md ./

# Some little PEP582 magic
RUN mkdir __pypackages__ && pdm sync --prod --no-editable -v

# When upgrading Python versions, please update '.python-version' to match
FROM python:3.10.12-slim

ENV DEBIAN_FRONTEND=noninteractive

ARG SHA=develop
ENV SHA="${SHA}"

ARG BUILDTIME=unknown
ENV BUILDTIME="${BUILDTIME}"

#COPY --from=py-deps /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages

ENV PYTHONPATH="${PYTHONPATH}":/python/pkgs
COPY --from=py-deps /python/__pypackages__/3.10/lib /python/pkgs
COPY --from=py-deps /python/__pypackages__/3.10/bin/* /bin/

RUN apt-get update && apt-get -y upgrade && apt-get -y install curl jq && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /home/jf_agent && \
    useradd --home-dir /home/jf_agent --shell /bin/bash --user-group jf_agent && \
    chown -R jf_agent:jf_agent /home/jf_agent

COPY --chown=jf_agent:jf_agent . /home/jf_agent

WORKDIR /home/jf_agent

USER jf_agent

ENTRYPOINT ["./rollback_on_fail.sh"]
