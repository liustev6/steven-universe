# syntax=docker/dockerfile:1
# sandbox-runner image. Build context is python/ so the shared library is available:
#   docker build -f services/sandbox-runner/Dockerfile python/
# (compose.yaml in this directory does this for you.) Spec: docs/specs/agent-service/sandbox-runner.md.

FROM python:3.11-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY services/sandbox-runner/requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install -r /tmp/requirements.txt
COPY libs/service-auth /tmp/service-auth
RUN /venv/bin/pip install "/tmp/service-auth[fastapi]"

FROM python:3.11-slim
ARG REVISION=unknown
LABEL org.opencontainers.image.title="sandbox-runner" \
      org.opencontainers.image.source="https://github.com/liustev6/steven-universe" \
      org.opencontainers.image.revision="${REVISION}"
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# xfsprogs: xfs_quota, for the workspaces' quotas (R13).
RUN apt-get update \
 && apt-get install -y --no-install-recommends xfsprogs \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /venv /venv
WORKDIR /app
COPY services/sandbox-runner/app ./app

# Runs as root: it drives the VM's Docker (the socket) and XFS quotas. That makes it root on the VM,
# which exists only for sandboxes (R3); the container is for packaging, not isolation.
EXPOSE 8443

# Liveness over loopback. The certificate is for sandbox-runner.lan.liustev6.ca, not 127.0.0.1, so
# this one probe skips verification; it sends nothing secret.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

# A stop destroys every pod (R9, "Deploying the runner"): uvicorn stops waiting for open requests
# after 5 s so that shutdown runs well within compose's stop_grace_period.
CMD ["uvicorn", "app.api:app", "--factory", \
     "--host", "0.0.0.0", "--port", "8443", "--no-server-header", "--timeout-graceful-shutdown", "5", \
     "--ssl-certfile", "/etc/sandbox-runner/tls/cert.pem", \
     "--ssl-keyfile", "/etc/sandbox-runner/tls/key.pem"]
