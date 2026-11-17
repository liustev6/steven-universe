"""The sessions API (port 443): what callers use (control-plane.md "The sessions API").

    sessions.py        start, turns, events, state, cancel, end           chat:write / chat:read
    conversations.py   history: conversations and their turns             chat:read
    admin.py           the kill switch, and /v1/status                    chat:admin / status:read
    stream.py          protocol B as Server-Sent Events (id = seq, so Last-Event-ID resumes)
    errors.py          errors as RFC 9457 problem details

Callers authenticate with auth-service tokens (audience agent-service); `auth` is service-auth's
ServiceAuth, or a stand-in in tests.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api import admin, conversations, errors, sessions as sessions_routes
from app.sessions import Sessions

AUDIENCE = "agent-service"


def create_api(sessions: Sessions, auth) -> FastAPI:
    app = FastAPI(title="agent-service", docs_url=None, redoc_url=None, openapi_url=None)
    errors.install(app)
    app.include_router(sessions_routes.build_router(sessions, auth))
    app.include_router(conversations.build_router(sessions, auth))
    app.include_router(admin.build_router(sessions, auth))

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    return app
