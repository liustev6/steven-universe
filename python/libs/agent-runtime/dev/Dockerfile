# syntax=docker/dockerfile:1
# The milestone-1 dev gateway (temporary). Build context: python/libs (for agent-runtime and service-auth).
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt
COPY agent-runtime/pyproject.toml agent-runtime/pyproject.toml
COPY agent-runtime/core agent-runtime/core
COPY agent-runtime/dev/pyproject.toml dev/pyproject.toml
COPY agent-runtime/dev/agent_dev dev/agent_dev
COPY service-auth service-auth
# Without dependencies, then exactly the gateway's own: core.types from agent-runtime; the Codex SDK
# for the auth broker (device-code login, token refresh; model-access.md); service-auth to call the
# sandbox runner (runner mode, CP2).
RUN pip install --no-deps ./agent-runtime ./dev ./service-auth \
 && pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.28.1" "pyyaml>=6" "pydantic>=2.10" \
      "pyjwt[crypto]>=2.10.1" "openai-codex==0.167.2"
EXPOSE 8080
CMD ["uvicorn", "agent_dev.gateway:app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
