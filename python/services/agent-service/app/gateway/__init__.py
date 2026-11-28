"""The gateway (port 8443): the only thing a sandbox can reach (control-plane.md C1, "The gateway").

    pairing.py   who is calling: capability + the pod's X-Sandbox-Id (C7)
    runtime.py   GET /v1/runtime/turns, POST /v1/runtime/events          (protocol A)
    model.py     /model/{path}: OpenAI with your login, within budget     (C11, C13)
    mcp.py       /mcp: the session's tools-service tools                 (tools.md)
    dav.py       /dav: the session's files, read-only, for its mounter   (files-and-mounts.md)
    files.py     /files: `sfsync put` / `rm`, checked here                (files-and-mounts.md F7)
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.broker import ModelLogin
from app.gateway import dav, files, mcp, model, runtime
from app.gateway.pairing import runtime_dependency
from app.sessions import Sessions


def create_gateway(sessions: Sessions, login: ModelLogin, upstream: httpx.AsyncClient) -> FastAPI:
    """`app.state.mcp`: the /mcp server; whoever serves the app runs its `session_manager.run()`."""
    app = FastAPI(title="agent-service gateway", docs_url=None, redoc_url=None, openapi_url=None)
    paired = runtime_dependency(sessions)
    app.include_router(runtime.build_router(sessions, paired))
    app.include_router(model.build_router(sessions, login, upstream, paired))
    app.include_router(dav.build_router(sessions, paired))
    app.include_router(files.build_router(sessions, paired))
    app.state.mcp, route = mcp.create_mcp(sessions)
    app.router.routes.append(route)
    return app
