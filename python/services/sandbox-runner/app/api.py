"""The runner's API, for agent-service only (sandbox-runner.md "API").

    POST /v1/sandboxes/claim             {session_id, capability, profile} -> {sandbox_id, gateway_url}; 503 if none
    POST /v1/sandboxes/{id}/release      destroy it (and warm up a replacement)
    GET  /v1/sandboxes                   the pool
    GET  /health                         no auth: is the pool at its target?

There is no route to run a command in a sandbox, read its files or change its network: the runner
can only create and destroy. Callers: a dev token until 2.5 (auth-service, aud=sandbox-runner).
"""

from __future__ import annotations

import hmac
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.pool import NoSandbox, Pool


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=16, max_length=512)
    profile: dict[str, Any]          # passed through to the session file; agent-runtime validates it


def dev_token_auth(settings: Settings) -> Callable[[Request], None]:
    token = settings.DEV_TOKEN_FILE.read_text().strip() if settings.DEV_TOKEN_FILE else None

    def authorize(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        presented = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(presented, token):
            raise HTTPException(401, "invalid token")
    return authorize


def create_app(settings: Settings, pool: Pool, authorize: Callable[[Request], None] | None = None) -> FastAPI:
    authorize = authorize or dev_token_auth(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await pool.start()
        yield
        await pool.stop()

    app = FastAPI(title="sandbox-runner", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    auth = [Depends(authorize)]

    @app.post("/v1/sandboxes/claim", dependencies=auth)
    async def claim(body: ClaimRequest) -> dict:
        try:
            pod = await pool.claim(body.session_id, body.capability, body.profile)
        except NoSandbox as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
        return {"sandbox_id": str(pod.id), "gateway_url": pod.gateway_url}

    @app.post("/v1/sandboxes/{sandbox_id}/release", dependencies=auth)
    async def release(sandbox_id: uuid.UUID) -> dict:
        if not await pool.release(sandbox_id):
            raise HTTPException(404, "no claimed sandbox with that id")
        return {"ok": True}

    @app.get("/v1/sandboxes", dependencies=auth)
    async def sandboxes() -> dict:
        now = time.monotonic()
        return {"warm": pool.count("warm"), "starting": pool.count("starting"), "claimed": pool.count("claimed"),
                "target_warm": settings.WARM, "max": settings.MAX, "sandbox_image": settings.SANDBOX_IMAGE,
                "pods": [p.describe(now) for p in pool.pods.values()]}

    @app.get("/health")
    async def health() -> dict:
        return {"ok": pool.count("warm") >= min(settings.WARM, settings.MAX - pool.count("claimed")),
                "warm": pool.count("warm"), "claimed": pool.count("claimed")}

    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory (the runner's container, 2.6)
    from app.backend import DockerBackend
    from app.workspaces import XfsWorkspaces

    settings = Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    workspaces = XfsWorkspaces(settings.WORKSPACE_ROOT, settings.WORKSPACE_QUOTA_MB, settings.SANDBOX_UID)
    return create_app(settings, Pool(settings, DockerBackend(), workspaces))
