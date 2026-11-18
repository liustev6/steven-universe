# syntax=docker/dockerfile:1
# speech-service image: our code on speech-base (the model stack, python/images/speech-base). Build
# context is python/ so the shared library is available:
#   docker build -f services/speech-service/Dockerfile python/
# Spec: docs/specs/speech-service.md. The models' weights are not in the image: the deploy's hook puts
# them, pinned and checked, in /models (mounted read-only).
#
# Only COPY steps: BuildKit builds and pushes this without pulling the ~7 GB base (its layers are
# referenced, not downloaded). service-auth is pure Python: copied onto the path, its dependencies
# (PyJWT, cryptography, httpx, fastapi) are in the base.

# speech-base, pinned by digest: bumped by hand after CI publishes a new base (infra/images/speech-base.yaml).
ARG BASE=ghcr.io/liustev6/steven-universe/speech-base@sha256:0000000000000000000000000000000000000000000000000000000000000000
FROM ${BASE}
ARG REVISION=unknown
LABEL org.opencontainers.image.title="speech-service" \
      org.opencontainers.image.source="https://github.com/liustev6/steven-universe" \
      org.opencontainers.image.revision="${REVISION}"
# HOME and Triton's kernel cache on the tmpfs (the root filesystem is read-only): kernels compile at
# the warm-up, before the service is healthy.
ENV PYTHONPATH=/app:/libs \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TRITON_CACHE_DIR=/tmp/triton \
    TRITON_DEFAULT_FP_FUSION=1

COPY libs/service-auth/service_auth /libs/service_auth
WORKDIR /app
COPY services/speech-service/app ./app

# Runs as uid 10001 (compose.yaml), with the GPU (CT 108's devN entries, the NVIDIA container toolkit).
EXPOSE 8443

# Liveness over loopback: 200 once both models are loaded and warm (~15 s; the first start on a host
# compiles the kernels too). The certificate is for speech.lan.liustev6.ca, not 127.0.0.1, so this one
# probe skips verification; it sends nothing secret.
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=3 \
  CMD ["python", "-c", "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=4)"]

CMD ["python", "-m", "app.main"]
