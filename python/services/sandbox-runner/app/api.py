"""The runner's API, for agent-service only (sandbox-runner.md "API").

    POST /v1/sandboxes/claim             {template, session_id, capability, profile} -> {sandbox_id, gateway_url}
                                         400 unknown template; 503 if none of that template
    POST /v1/sandboxes/{id}/release      destroy it (and warm up a replacement)
    GET  /v1/sandboxes                   the pool, per template
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
from app.pool import NoSandbox, Pool, UnknownTemplate


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=32)    # a name from the runner's templates, never an image
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
            pod = await pool.claim(body.template, body.session_id, body.capability, body.profile)
        except UnknownTemplate:
            raise HTTPException(400, f"unknown template: {body.template}")
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
        templates = {name: {"image": t.image, "runtime": t.runtime, "target_warm": t.warm, "max": pool.cap(name),
                            **{state: pool.count(state, template=name) for state in ("warm", "starting", "claimed")}}
                     for name, t in pool.templates.items()}
        return {"max": settings.MAX, "in_use": len(pool.pods), "templates": templates,
                "pods": [p.describe(now) for p in pool.pods.values()]}

    @app.get("/health")
    async def health() -> dict:
        def satisfied(name: str, t) -> bool:      # its warm target, or as many as its caps still allow
            room = min(pool.cap(name) - pool.count("claimed", template=name), settings.MAX - pool.count("claimed"))
            return pool.count("warm", template=name) >= min(t.warm, max(room, 0))
        return {"ok": all(satisfied(n, t) for n, t in pool.templates.items()),
                "warm": {n: pool.count("warm", template=n) for n in pool.templates},
                "claimed": pool.count("claimed")}

    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory (the runner's container, 2.6)
    from app.backend import DockerBackend
    from app.templates import load_templates
    from app.workspaces import XfsWorkspaces

    settings = Settings()
    logging.basicConfig(level=settings.LOG_LEVEL, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    templates = load_templates(settings.TEMPLATES_FILE)
    workspaces = XfsWorkspaces(settings.WORKSPACE_ROOT, settings.SANDBOX_UID)
    return create_app(settings, Pool(settings, templates, DockerBackend(), workspaces))
