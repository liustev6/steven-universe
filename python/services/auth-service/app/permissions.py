"""Computing what a token may contain (spec: "How a token's permissions are computed").

1. Resolve the policy templates for (client, audience) with the available
   variables; templates whose variables can't be filled are dropped.
2. If the request asks for specific scopes / authorization details, each must
   be covered by an allowed one. The token gets what was asked for, or
   everything allowed if nothing was asked for. Tokens can only narrow (A9).
   At an audience with `grant_overlap`, a request asking for more gets the
   overlap of what it asked for and what is allowed instead (RFC 6749 §3.3);
   nothing in common is still refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import invalid_details, invalid_scope, invalid_target
from .policy import AudienceDef, Policy
from .validation import covers, location_error, substitute

MAX_IDENTIFIER_LENGTH = 128
DETAIL_FIELDS = {"type", "actions", "locations", "identifier"}


@dataclass(frozen=True)
class Permissions:
    scopes: tuple[str, ...]
    details: tuple[dict[str, Any], ...]


def resolve(
    policy: Policy,
    template_owner: str,
    audience: str,
    variables: dict[str, str],
) -> Permissions:
    """What `template_owner`'s grant at `audience` allows, with variables filled in."""
    aud = policy.audiences.get(audience)
    client = policy.clients.get(template_owner)
    grant = client.grants.get(audience) if client else None
    if aud is None or grant is None:
        raise invalid_target(f"no grant for audience {audience!r}")

    scopes = tuple(grant.scopes)
    details: list[dict[str, Any]] = []
    for template in grant.authorization_details:
        locations = [substitute(loc, variables) for loc in template.locations]
        if any(loc is None for loc in locations):
            continue  # e.g. ${user} in a token that doesn't act for a user
        details.append(
            {"type": template.type, "actions": list(template.actions), "locations": locations}
        )
    return Permissions(scopes, tuple(details))


def parse_requested_details(raw: Any, aud: AudienceDef) -> list[dict[str, Any]]:
    """Validate the `authorization_details` request parameter (RFC 9396 §2)."""
    if not isinstance(raw, list) or not raw:
        raise invalid_details("must be a non-empty JSON array")
    parsed: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise invalid_details("each entry must be an object")
        unknown = set(item) - DETAIL_FIELDS
        if unknown:
            raise invalid_details(f"unsupported fields {sorted(unknown)}")
        dtype = aud.detail_types.get(item.get("type"))  # type: ignore[arg-type]
        if dtype is None:
            raise invalid_details(f"unknown type {item.get('type')!r} for this audience")
        actions = item.get("actions")
        if (
            not isinstance(actions, list)
            or not actions
            or not all(isinstance(a, str) for a in actions)
            or set(actions) - set(dtype.actions)
        ):
            raise invalid_details(f"actions must be a non-empty subset of {dtype.actions}")
        locations = item.get("locations")
        if not isinstance(locations, list) or not locations:
            raise invalid_details("locations must be a non-empty array")
        for location in locations:
            problem = location_error(location) if isinstance(location, str) else "not a string"
            if problem:
                raise invalid_details(f"bad location: {problem}")
        detail: dict[str, Any] = {
            "type": item["type"],
            "actions": list(dict.fromkeys(actions)),
            "locations": list(dict.fromkeys(locations)),
        }
        if "identifier" in item:
            identifier = item["identifier"]
            if (
                not isinstance(identifier, str)
                or not 0 < len(identifier) <= MAX_IDENTIFIER_LENGTH
                or not identifier.isprintable()
            ):
                raise invalid_details("identifier must be a short printable string")
            detail["identifier"] = identifier
        parsed.append(detail)
    return parsed


def _detail_covered(requested: dict[str, Any], allowed: dict[str, Any]) -> bool:
    if requested["type"] != allowed["type"]:
        return False
    if not set(requested["actions"]) <= set(allowed["actions"]):
        return False
    if "identifier" in allowed and requested.get("identifier") != allowed["identifier"]:
        return False
    return all(
        any(covers(a, r) for a in allowed["locations"]) for r in requested["locations"]
    )


def _location_overlap(allowed: str, requested: str) -> str | None:
    """The narrower of two locations when one covers the other, else None."""
    if covers(allowed, requested):
        return requested
    if covers(requested, allowed):
        return allowed
    return None


def _detail_overlaps(requested: dict[str, Any], allowed: dict[str, Any]) -> dict[str, Any] | None:
    """What `requested` and `allowed` have in common, as one detail, or None."""
    if requested["type"] != allowed["type"]:
        return None
    if "identifier" in allowed and requested.get("identifier") != allowed["identifier"]:
        return None
    actions = [a for a in requested["actions"] if a in allowed["actions"]]
    locations = [
        loc for r in requested["locations"] for a in allowed["locations"]
        if (loc := _location_overlap(a, r)) is not None
    ]
    if not actions or not locations:
        return None
    detail: dict[str, Any] = {
        "type": requested["type"], "actions": actions, "locations": list(dict.fromkeys(locations)),
    }
    identifier = requested.get("identifier", allowed.get("identifier"))
    if identifier is not None:
        detail["identifier"] = identifier
    return detail


def overlap(
    allowed: Permissions,
    requested_scopes: list[str] | None,
    requested_details: list[dict[str, Any]] | None,
) -> Permissions:
    """What the request and `allowed` have in common (grant_overlap audiences); refused if nothing."""
    if requested_scopes is not None:
        scopes = tuple(s for s in dict.fromkeys(requested_scopes) if s in allowed.scopes)
    else:
        scopes = allowed.scopes
    if not scopes:
        raise invalid_scope("none of the requested scopes is allowed")

    if requested_details is not None:
        found = [
            d for r in requested_details for a in allowed.details
            if (d := _detail_overlaps(r, a)) is not None
        ]
        details = tuple({repr(sorted(d.items())): d for d in found}.values())  # duplicates once
        if not details:
            raise invalid_details("none of the requested authorization details is allowed")
    else:
        details = allowed.details
    return Permissions(scopes, details)


def narrow(
    allowed: Permissions,
    requested_scopes: list[str] | None,
    requested_details: list[dict[str, Any]] | None,
) -> Permissions:
    """The requested subset of `allowed`, or all of `allowed` if nothing was requested."""
    if requested_scopes is not None:
        extra = [s for s in requested_scopes if s not in allowed.scopes]
        if extra:
            raise invalid_scope(f"not allowed: {' '.join(extra)}")
        scopes = tuple(dict.fromkeys(requested_scopes))
    else:
        scopes = allowed.scopes
    if not scopes:
        raise invalid_scope("no scopes are allowed for this request")

    if requested_details is not None:
        for detail in requested_details:
            if not any(_detail_covered(detail, a) for a in allowed.details):
                raise invalid_details(
                    f"not covered by what is allowed: {detail['type']} "
                    f"{detail['actions']} {detail['locations']}"
                )
        details = tuple(requested_details)
    else:
        details = allowed.details
    return Permissions(scopes, details)
