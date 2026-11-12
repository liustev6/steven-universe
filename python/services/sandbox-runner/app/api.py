"""The runner's API, for agent-service only (sandbox-runner.md "API").

    POST /v1/sandboxes/claim             {template, session_id, capability, profile} -> {sandbox_id, gateway_url}
                                         400 unknown template; 503 if none of that template
    POST /v1/sandboxes/{id}/release      destroy it (and warm up a replacement)
    GET  /v1/sandboxes                   the pool, per template
    GET  /health                         no auth: is the pool at its target?

There is no route to run a command in a sandbox, read its files or change its network: the runner
can only create and destroy.

Callers authenticate with auth-service tokens (audience `sandbox-runner`), checked locally against
auth-service's public keys (service-auth). Each route needs its scope; auth-service's policy decides
which clients get them: agent-service only (in CP2, its stand-in `agent-dev`).
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Protocol

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.pool import NoSandbox, Pool, UnknownTemplate


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(min_length=1, max_length=32)    # a name from the runner's templates, never an image
    session_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=16, max_length=512)
    profile: dict[str, Any]          # passed through to the session file; agent-runtime validates it


AUDIENCE = "sandbox-runner"


class Auth(Protocol):
    """service_auth.fastapi_auth.ServiceAuth, or a stand-in in tests."""
    def require(self, *scopes: str): ...


def create_app(settings: Settings, pool: Pool, auth: Auth | None = None) -> FastAPI:
    if auth is None:
        from service_auth.fastapi_auth import ServiceAuth
        auth = ServiceAuth.from_env(AUDIENCE)              # SERVICE_AUTH_ISSUER

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await pool.start()
        yield
        await pool.stop()

    app = FastAPI(title="sandbox-runner", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool

    @app.post("/v1/sandboxes/claim", dependencies=[Depends(auth.require("sandboxes:claim"))])
    async def claim(body: ClaimRequest) -> dict:
        try:
            pod = await pool.claim(body.template, body.session_id, body.capability, body.profile)
        except UnknownTemplate:
            raise HTTPException(400, f"unknown template: {body.template}")
        except NoSandbox as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": "5"})
        return {"sandbox_id": str(pod.id), "gateway_url": pod.gateway_url}

    @app.post("/v1/sandboxes/{sandbox_id}/release", dependencies=[Depends(auth.require("sandboxes:release"))])
    async def release(sandbox_id: uuid.UUID) -> dict:
        if not await pool.release(sandbox_id):
            raise HTTPException(404, "no claimed sandbox with that id")
        return {"ok": True}

    @app.get("/v1/sandboxes", dependencies=[Depends(auth.require("sandboxes:read"))])
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
