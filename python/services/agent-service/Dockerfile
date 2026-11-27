# syntax=docker/dockerfile:1
# agent-service image. Build context is python/ so the shared libraries are available:
#   docker build -f services/agent-service/Dockerfile python/
# (compose.yaml in this directory does this for you.) Spec: docs/specs/agent-service/control-plane.md.

FROM python:3.11-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY services/agent-service/requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install -r /tmp/requirements.txt
COPY libs/service-auth /tmp/service-auth
RUN /venv/bin/pip install "/tmp/service-auth[fastapi]"
# Protocol A's types (core.types), and the Codex SDK with its codex binary: the auth broker signs in
# and refreshes through it.
COPY libs/agent-runtime /tmp/agent-runtime
RUN /venv/bin/pip install /tmp/agent-runtime
# Protocol B, what callers receive (shared_schemas.agent_service): shared with them.
COPY libs/shared-schemas /tmp/shared-schemas
RUN /venv/bin/pip install /tmp/shared-schemas

FROM python:3.11-slim
ARG REVISION=unknown
LABEL org.opencontainers.image.title="agent-service" \
      org.opencontainers.image.source="https://github.com/liustev6/steven-universe" \
      org.opencontainers.image.revision="${REVISION}"
# HOME on the tmpfs: the codex binary may write caches there (the root filesystem is read-only).
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

COPY --from=build /venv /venv
WORKDIR /app
COPY services/agent-service/app ./app

# Runs as uid 10001 (compose.yaml). 8443: the sessions API (host 443); 9443: the gateway (host 8443).
EXPOSE 8443 9443

# Liveness over loopback. The certificate is for agent.lan.liustev6.ca, not 127.0.0.1, so this one
# probe skips verification; it sends nothing secret.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

CMD ["python", "-m", "app.main"]
