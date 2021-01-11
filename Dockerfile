FROM python:3.7.4 AS py-deps
COPY ./Pipfile ./Pipfile.lock ./
RUN pip install pipenv && \
    pipenv install --deploy --system --ignore-pipfile --clear

FROM python:3.7.4-slim

ARG SHA=develop
ENV SHA="${SHA}"

COPY --from=py-deps /usr/local/lib/python3.7/site-packages /usr/local/lib/python3.7/site-packages
RUN pip install awscli && \
    mkdir -p /home/jf_agent && \
    useradd --home-dir /home/jf_agent --shell /bin/bash --user-group jf_agent && \
    chown -R jf_agent:jf_agent /home/jf_agent

COPY --chown=jf_agent:jf_agent . /home/jf_agent
RUN rm /home/jf_agent/Pipfile /home/jf_agent/Pipfile.lock
WORKDIR /home/jf_agent
ENV PYTHONPATH=/home/jf_agent

# Run as last command to prevent caching
ARG TIMESTAMP=$(date -u "+%Y%m%dT%H%M%SZ")
ENV BUILD_TIMESTAMP="${TIMESTAMP}"

USER jf_agent
ENTRYPOINT ["python", "jf_agent/main.py"]
