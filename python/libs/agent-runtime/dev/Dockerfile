# syntax=docker/dockerfile:1
# The milestone-1 dev gateway (temporary). Build context: python/libs/agent-runtime.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt
COPY pyproject.toml agent-runtime/pyproject.toml
COPY core agent-runtime/core
COPY dev/pyproject.toml dev/pyproject.toml
COPY dev/agent_dev dev/agent_dev
# Both without dependencies, then exactly the gateway's own: core.types from agent-runtime, and the
# Codex SDK for the auth broker (device-code login, token refresh; model-access.md).
RUN pip install --no-deps ./agent-runtime ./dev \
 && pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.28.1" "pyyaml>=6" "pydantic>=2.10" \
      "openai-codex==0.167.2"
EXPOSE 8080
CMD ["uvicorn", "agent_dev.gateway:app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
