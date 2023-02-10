FROM python:3.9.14 AS py-deps
COPY ./Pipfile ./Pipfile.lock ./
RUN pip install -U pip setuptools pipenv && \
    pipenv install --deploy --system --ignore-pipfile --clear

# When upgrading Python versions, please update '.python-version' to match
FROM python:3.12.0a5-slim

ENV DEBIAN_FRONTEND=noninteractive

ARG SHA=develop
ENV SHA="${SHA}"

ARG BUILDTIME=unknown
ENV BUILDTIME="${BUILDTIME}"

COPY --from=py-deps /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages

RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /home/jf_agent && \
    useradd --home-dir /home/jf_agent --shell /bin/bash --user-group jf_agent && \
    chown -R jf_agent:jf_agent /home/jf_agent

COPY --chown=jf_agent:jf_agent . /home/jf_agent
RUN rm /home/jf_agent/Pipfile /home/jf_agent/Pipfile.lock
WORKDIR /home/jf_agent
ENV PYTHONPATH=/home/jf_agent

USER jf_agent
ENTRYPOINT ["python", "jf_agent/main.py"]
