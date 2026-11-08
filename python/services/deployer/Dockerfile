# syntax=docker/dockerfile:1
# deployer image. Build context is python/ so the shared library is available:
#   docker build -f services/deployer/Dockerfile python/
# (compose.yaml in this directory does this for you.)

FROM python:3.11-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY services/deployer/requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install -r /tmp/requirements.txt
COPY libs/service-auth /tmp/service-auth
RUN /venv/bin/pip install "/tmp/service-auth[fastapi]"

FROM python:3.11-slim
ARG REVISION=unknown
LABEL org.opencontainers.image.title="deployer" \
      org.opencontainers.image.source="https://github.com/liustev6/steven-universe" \
      org.opencontainers.image.revision="${REVISION}"
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

# git: the read-only clone of the repository (manifests at the deployed commit).
# openssh-client: deploy-target on the hosts, with the restricted key.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/*

# Fixed uid/gid: /etc/deployer and /var/lib/deployer on the host are owned by 10001.
RUN groupadd --system --gid 10001 deployer \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin deployer

COPY --from=build /venv /venv
WORKDIR /app
COPY services/deployer/app ./app

USER 10001:10001
EXPOSE 8443

# Liveness over loopback. The certificate is for deploy.lan.liustev6.ca, not 127.0.0.1, so this
# one probe skips verification; it sends nothing secret.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

CMD ["uvicorn", "app.main:app", "--factory", \
     "--host", "0.0.0.0", "--port", "8443", "--no-server-header", \
     "--timeout-graceful-shutdown", "900", \
     "--ssl-certfile", "/etc/deployer/tls/cert.pem", \
     "--ssl-keyfile", "/etc/deployer/tls/key.pem"]
