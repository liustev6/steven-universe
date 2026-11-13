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

    page.py      the targets, reading and judging each, the merge and the cache
    routes.py    the route: api-service's own token for `status` targets, the rate limit
"""

from app.status.routes import build_router

__all__ = ["build_router"]
