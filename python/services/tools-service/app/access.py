"""The one access decision, for both interfaces (tools.md TL6, TL13).

auth-service decides what a caller may use and puts it in the token; this module only reads it.
A tool may be called when the token has the scope `tools:call` AND a `tool_access` detail with the
action `call` on a location covering the tool's (`weather/` covers `weather/forecast`; an exact
location covers only itself). It is an allowlist: a token without `tool_access` details gets no
tools, never all of them (auth-service refuses to mint one anyway: `requires_details`).
"""

from __future__ import annotations

from service_auth import Principal

SCOPE = "tools:call"
DETAIL_TYPE = "tool_access"


def granted(principal: Principal) -> tuple[str, ...]:
    """The locations this token may call; () when it may call nothing."""
    if SCOPE not in principal.scopes:
        return ()
    locations: list[str] = []
    for detail in principal.authorization_details:
        if detail.get("type") == DETAIL_TYPE and "call" in (detail.get("actions") or ()):
            locations.extend(loc for loc in detail.get("locations") or () if isinstance(loc, str) and loc)
    return tuple(locations)


def covers(grant: str, location: str) -> bool:
    """auth-service's prefix rule: a location ending in "/" covers what's under it; else only itself."""
    return location.startswith(grant) if grant.endswith("/") else location == grant


def may_call(principal: Principal, location: str) -> bool:
    return any(covers(g, location) for g in granted(principal))
