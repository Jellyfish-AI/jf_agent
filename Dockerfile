# Build stage
# When upgrading Python versions, update '.python-version' to match
FROM python:3.12.11 AS builder

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
FROM python:3.12.11-slim

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

# nonroot is the standard user in distroless images and hardened distros
RUN groupadd --gid 65532 nonroot && \
    useradd --uid 65532 --gid 65532 --home-dir /home/nonroot --shell /bin/bash nonroot && \
    mkdir -p /home/nonroot && \
    chown -R nonroot:nonroot /home/nonroot

WORKDIR /home/nonroot
USER nonroot

ENTRYPOINT ["python", "-m", "jf_agent.rollback_on_fail"]
