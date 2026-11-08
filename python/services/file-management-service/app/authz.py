"""The single access decision: `authorize(principal, action, bucket, key)` (spec D5, D7).

auth-service decides what a caller may do and puts it in the token; this module only enforces
it. Two levels must both allow an operation:

1. the coarse scope (`files:<action>`) in the token's `scope`, and
2. a `file_access` grant in `authorization_details` with that action on a location covering
   `<bucket>/<key>`.

Keys are validated first (key hygiene), so path tricks never reach the decision.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from service_auth import Principal

Action = Literal["read", "write", "delete", "list"]
DETAIL_TYPE = "file_access"
MAX_KEY_BYTES = 1024

audit = logging.getLogger("file_management.audit")


class BadKey(ValueError):
    """The bucket or key is not acceptable (400)."""


class Forbidden(Exception):
    """The token doesn't grant this action on this resource (403)."""


def resource_name(bucket: str, key: str, buckets: list[str], *, prefix: bool = False) -> str:
    """Validate a bucket + key (or list prefix) and return `<bucket>/<key>`.

    S3 treats keys as literal strings, so `private/a/../b/x` really is a key under `private/a/`.
    Anything that later normalizes paths could disagree, so such keys are refused outright.
    """
    if bucket not in buckets:
        raise BadKey("unknown bucket")
    if len(key.encode()) > MAX_KEY_BYTES:
        raise BadKey("key too long")
    if key.startswith("/") or "\\" in key:
        raise BadKey("invalid key")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in key):
        raise BadKey("invalid key")
    segments = key.split("/")
    if prefix and segments and segments[-1] == "":
        segments = segments[:-1]  # a prefix may end with "/"
    if not prefix and key == "":
        raise BadKey("empty key")
    if any(seg in ("", ".", "..") for seg in segments):
        raise BadKey("invalid key")
    return f"{bucket}/{key}"


def _covers(location: str, resource: str, *, listing: bool) -> bool:
    if location.endswith("/"):
        return resource.startswith(location)
    return not listing and resource == location  # an exact-object grant never allows listing


def authorize(p: Principal, action: Action, resource: str) -> None:
    """Allow or raise Forbidden; every decision is audited."""
    allowed = False
    identifier = None
    # Same scope rule as the route's require() (including the dev principal's "*").
    if p.has_scopes(f"files:{action}"):
        for detail in p.details(DETAIL_TYPE):
            if action in detail.get("actions", ()) and any(
                _covers(loc, resource, listing=action == "list") for loc in detail.get("locations", ())
            ):
                allowed, identifier = True, detail.get("identifier")
                break
    audit.info(json.dumps({
        "event": "allow" if allowed else "deny",
        "action": action,
        "resource": resource,
        "service": p.service,
        "on_behalf_of": p.on_behalf_of,
        "actor_chain": list(p.actor_chain),
        "client_id": p.client_id,
        "jti": p.token_id,
        "identifier": identifier,
    }, separators=(",", ":")))
    if not allowed:
        raise Forbidden(f"{action} not granted on {resource}")
