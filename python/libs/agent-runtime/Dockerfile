# syntax=docker/dockerfile:1
# The sandbox image: agent-runtime + the Codex SDK (with its native `codex` binary; no Node.js).
# One image for every profile. Build context: this folder.
#   docker build -t agent-runtime .
FROM python:3.11-slim
ARG REVISION=unknown
LABEL org.opencontainers.image.title="agent-runtime" \
      org.opencontainers.image.source="https://github.com/liustev6/steven-universe" \
      org.opencontainers.image.revision="${REVISION}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp/home CODEX_HOME=/tmp/codex-home

# Fixed uid/gid like every service; the sandbox never runs as root.
RUN groupadd --system --gid 10001 agent \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin agent

WORKDIR /opt/agent-runtime
COPY pyproject.toml ./
COPY core ./core
RUN pip install . && mkdir -p /workspace && chown 10001:10001 /workspace

USER 10001:10001
WORKDIR /workspace
# Waits for /run/session/session.json, then serves the session (agent-runtime.md).
ENTRYPOINT ["python", "-m", "core"]
