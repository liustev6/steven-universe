"""GET /v1/status: the lab's public status page (agent-service tracker, "S").

Public on purpose (a personal lab): anyone may see whether each service is up. What it shows is
public-safe: each service's own health (designed to be public), OpenBao's sealed state, and the
sandbox runner's pods without any id. Never sessions, prompts, tokens, secrets, internal addresses.

    targets (config.env, API_STATUS_TARGETS), read in parallel, each with a timeout:
      health    a service's /health (no token): ok if it says "ok"/"healthy"
      openbao   OpenBao's /v1/sys/health: ok if initialized and unsealed; only those and its version kept
      status    a service's /v1/status, with api-service's own token (scope status:read)
    -> {"ok": all ok, "checked_at": …, "services": {name: {"ok", "latency_ms", "details" | "error"}}}

A slow or failing service is reported, never fails the page. The result is cached a few seconds, so
however often it's asked, the services are read at most once per period; callers are rate-limited too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.edge import Mode, access, client_ip
from app.ratelimit import RateLimiter

logger = logging.getLogger("api_service.status")
STARTED = time.monotonic()


class StatusTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str
    kind: Literal["health", "openbao", "status"]
    audience: str | None = None          # kind status: the audience of api-service's token


class StatusPage:
    def __init__(self, targets: list[StatusTarget], http: httpx.AsyncClient, *, revision: str,
                 auth_for: Callable[[str], httpx.Auth] | None, cache_seconds: float, timeout: float,
                 clock: Callable[[], float] = time.monotonic):
        self.targets, self.http, self.revision = targets, http, revision
        self.auth_for, self.cache_seconds, self.timeout, self.clock = auth_for, cache_seconds, timeout, clock
        self._cached: tuple[float, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, Any]:
        async with self._lock:               # concurrent callers share one round of checks
            if self._cached and self.clock() - self._cached[0] < self.cache_seconds:
                return self._cached[1]
            results = await asyncio.gather(*(self._check(t) for t in self.targets))
            services = {"api-service": {"ok": True, "details": {"version": self.revision[:7],
                                                                "uptime_s": round(time.monotonic() - STARTED)}}}
            services.update({t.name: r for t, r in zip(self.targets, results)})
            page = {"ok": all(s["ok"] for s in services.values()),
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "services": services}
            self._cached = (self.clock(), page)
            return page

    async def _check(self, t: StatusTarget) -> dict[str, Any]:
        started = time.monotonic()
        try:
            auth = self.auth_for(t.audience) if t.kind == "status" and self.auth_for and t.audience else None
            r = await self.http.get(t.url, auth=auth, timeout=self.timeout)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as exc:              # unreachable, timeout, no token: reported, never raised
            logger.info("status: %s unreachable: %r", t.name, exc)
            return {"ok": False, "error": "unreachable"}
        latency = round((time.monotonic() - started) * 1000)
        if t.kind == "openbao":
            details = {k: body.get(k) for k in ("initialized", "sealed", "version")}
            ok = r.status_code == 200 and body.get("initialized") is True and body.get("sealed") is False
        elif t.kind == "status":
            details = {k: v for k, v in body.items() if k != "ok"}
            ok = r.status_code == 200 and body.get("ok") is True
        else:                                 # health
            details = {k: v for k, v in body.items() if k not in ("status", "ok")}
            ok = r.status_code == 200 and (body.get("status") in ("ok", "healthy") or body.get("ok") is True)
        result: dict[str, Any] = {"ok": ok, "latency_ms": latency}
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}"
        if details:
            result["details"] = details
        return result


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
