"""Edge rules every route goes through (api-service spec E2, "Route access modes", "Request pipeline").

Deny by default: a route without an access mode is a bug, and a test walks app.routes to catch
it. Only two modes exist so far; `user`, `optional` and `client` arrive with user login and the
file routes (spec Migration).

    public   everyone; nothing downstream needs a credential
    proxy    forwarded unchanged to one allowlisted internal service, after pre-checks (today only
             /deploy/v1/deployments -> the deployer)
"""

from __future__ import annotations

import ipaddress
from enum import Enum
from typing import Callable

from fastapi import Request

from app.config import Settings


class Mode(str, Enum):
    PUBLIC = "public"
    PROXY = "proxy"


def access(mode: Mode) -> Callable[[], Mode]:
    """`dependencies=[Depends(access(Mode.PUBLIC))]`: declares the route's mode (checked by tests)."""

    def dependency() -> Mode:
        return mode

    dependency.access_mode = mode  # type: ignore[attr-defined]
    return dependency


def client_ip(request: Request, settings: Settings) -> str:
    """The caller's IP: Cloudflare's CF-Connecting-IP, but only when the request came from cloudflared."""
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("cf-connecting-ip", "").strip()
    if forwarded and peer in settings.TRUSTED_PROXIES:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer
