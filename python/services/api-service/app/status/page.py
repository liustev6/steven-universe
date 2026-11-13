"""The status page's logic: read each target, judge it, merge, cache (see the package docstring)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict

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
