"""The gateway (port 8443): the only thing a sandbox can reach (control-plane.md C1, "The gateway").

    pairing.py   who is calling: capability + the pod's X-Sandbox-Id (C7)
    runtime.py   GET /v1/runtime/turns, POST /v1/runtime/events          (protocol A)
    model.py     /model/{path}: OpenAI with your login, within budget     (C11, C13)
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.broker import ModelLogin
from app.gateway import model, runtime
from app.gateway.pairing import runtime_dependency
from app.sessions import Sessions


def create_gateway(sessions: Sessions, login: ModelLogin, upstream: httpx.AsyncClient) -> FastAPI:
    app = FastAPI(title="agent-service gateway", docs_url=None, redoc_url=None, openapi_url=None)
    paired = runtime_dependency(sessions)
    app.include_router(runtime.build_router(sessions, paired))
    app.include_router(model.build_router(sessions, login, upstream, paired))
    return app
