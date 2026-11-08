# syntax=docker/dockerfile:1
# file-management image. Build context is python/ so the shared library is available:
#   docker build -f services/file-management-service/Dockerfile python/
# (compose.yaml in this directory does this for you.)

FROM python:3.11-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY services/file-management-service/requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install -r /tmp/requirements.txt
COPY libs/service-auth /tmp/service-auth
RUN /venv/bin/pip install "/tmp/service-auth[fastapi]"

FROM python:3.11-slim
ARG REVISION=unknown
LABEL org.opencontainers.image.title="file-management" \
      org.opencontainers.image.source="https://github.com/liustev6/steven-universe" \
      org.opencontainers.image.revision="${REVISION}"
ENV PATH=/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fixed uid/gid: /etc/file-management (secrets, certificate) on the host is owned by 10001.
RUN groupadd --system --gid 10001 files \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin files

COPY --from=build /venv /venv
WORKDIR /app
COPY services/file-management-service/app ./app

USER 10001:10001
EXPOSE 8443

# Liveness over loopback (also checks storage). The certificate is for files.lan.liustev6.ca, not
# 127.0.0.1, so this one probe skips verification; it sends nothing secret.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

CMD ["uvicorn", "app.main:app", "--factory", \
     "--host", "0.0.0.0", "--port", "8443", "--no-server-header", \
     "--ssl-certfile", "/etc/file-management/tls/cert.pem", \
     "--ssl-keyfile", "/etc/file-management/tls/key.pem"]
