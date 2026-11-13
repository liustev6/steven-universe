"""GET /v1/status: public (mode `public`), rate-limited per caller; the page itself is page.py."""

from __future__ import annotations

from typing import Callable

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.edge import Mode, access, client_ip
from app.ratelimit import RateLimiter
from app.status.page import StatusPage


def build_router(settings: Settings, http: httpx.AsyncClient,
                 auth_for: Callable[[str], httpx.Auth] | None = None) -> APIRouter:
    router = APIRouter()
    targets = settings.STATUS_TARGETS
    if auth_for is None and any(t.kind == "status" for t in targets):
        from service_auth import TokenClient      # SERVICE_AUTH_ISSUER, _CLIENT_ID, _CLIENT_SECRET_FILE

        tokens = TokenClient.from_env()
        auths: dict[str, httpx.Auth] = {}

        def auth_for(audience: str) -> httpx.Auth:
            if audience not in auths:
                auths[audience] = tokens.auth(audience, scopes=["status:read"])
            return auths[audience]
    page = StatusPage(targets, http, revision=settings.REVISION, auth_for=auth_for,
                      cache_seconds=settings.STATUS_CACHE_SECONDS, timeout=settings.STATUS_TIMEOUT_SECONDS)
    limiter = RateLimiter(settings.STATUS_RATE_BURST, settings.STATUS_RATE_PER_MINUTE)

    @router.get("/v1/status", dependencies=[Depends(access(Mode.PUBLIC))])
    async def status(request: Request):
        ip = client_ip(request, settings)
        if not limiter.allow(ip):
            return JSONResponse({"error": "rate_limited", "message": "too many status requests",
                                 "request_id": getattr(request.state, "request_id", None)},
                                status_code=429, headers={"Retry-After": str(limiter.retry_after(ip))})
        return await page.get()

    return router
