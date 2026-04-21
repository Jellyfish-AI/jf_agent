# Build stage
# When upgrading Python versions, update '.python-version' to match
FROM python:3.13.11 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# hadolint ignore=DL3002
USER root

WORKDIR /python
COPY pyproject.toml uv.lock README.md ./
ENV UV_COMPILE_BYTECODE=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# uv will install python based on pyproject.toml. It won't use chainguards pythonn.
RUN uv venv /opt/venv
# hadolint ignore=DL3059
RUN uv sync --locked --no-dev --no-install-project --no-editable

# keep these layers separate from the sync above so we can change code without rebuilding the dependencies
COPY jf_agent ./jf_agent
RUN uv sync --locked --no-dev --no-editable

################################################################################
# Runtime stage
# When upgrading Python versions, update '.python-version' to match
FROM python:3.13.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG SHA=develop
ENV SHA="${SHA}"

ARG BUILDTIME=unknown
ENV BUILDTIME="${BUILDTIME}"

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
COPY --from=builder /opt/venv /opt/venv

# We name the user "jf_agent" (not "nonroot") so that /home/jf_agent is a real
# directory, matching the legacy documentation that tells customers to bind-mount
# to /home/jf_agent/config.yml and /home/jf_agent/output. Using a real directory
# (rather than a symlink) ensures `pwd` and `docker exec` show the expected path.
# The hardened image (Dockerfile.hardened) cannot rename Chainguard's built-in
# nonroot user, so it uses a symlink instead — see that file for details.
# UID/GID 65532 follows the distroless/hardened-image convention for nonroot.
RUN groupadd --gid 65532 jf_agent && \
    useradd --uid 65532 --gid 65532 --home-dir /home/jf_agent --shell /bin/bash jf_agent && \
    mkdir -p /home/jf_agent && \
    chown -R jf_agent:jf_agent /home/jf_agent

WORKDIR /home/jf_agent
USER jf_agent

ENTRYPOINT ["python", "-m", "jf_agent.rollback_on_fail"]
