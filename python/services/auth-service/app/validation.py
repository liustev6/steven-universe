"""Name and location rules shared by the policy, the token endpoint, and the CLI."""

from __future__ import annotations

import re

# Client ids and audiences: lowercase DNS-label style, never renamed or reused.
CLIENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
# User ids substituted into ${user}; no "/" or "." so they can't change a path.
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Template variables allowed in policy locations.
VARIABLE_RE = re.compile(r"\$\{([a-z]+)\}")
KNOWN_VARIABLES = frozenset({"client", "user"})

MAX_LOCATION_LENGTH = 1024


def location_error(location: str) -> str | None:
    """Why `location` is not a safe `<bucket>/<prefix>` string, or None if it is.

    Locations ending in "/" are prefixes; anything else names one exact object.
    Empty, "." and ".." segments, backslashes, control characters, and a leading
    "/" are rejected so that a string-prefix check and any later path
    normalisation can never disagree.
    """
    if not location:
        return "empty location"
    if len(location) > MAX_LOCATION_LENGTH:
        return "location too long"
    if location.startswith("/"):
        return "location must not start with '/'"
    if "\\" in location:
        return "location must not contain '\\'"
    if any(ord(c) < 32 or ord(c) == 127 for c in location):
        return "location must not contain control characters"
    segments = location.split("/")
    if location.endswith("/"):
        segments = segments[:-1]
    for segment in segments:
        if segment in ("", ".", ".."):
            return f"location has an empty, '.' or '..' segment: {location!r}"
    return None


def template_error(template: str) -> str | None:
    """Like `location_error`, for policy templates that may contain ${client} / ${user}."""
    for name in VARIABLE_RE.findall(template):
        if name not in KNOWN_VARIABLES:
            return f"unknown variable ${{{name}}} in {template!r}"
    if "$" in VARIABLE_RE.sub("", template):
        return f"stray '$' in {template!r}"
    return location_error(VARIABLE_RE.sub("x", template))


def substitute(template: str, variables: dict[str, str]) -> str | None:
    """Fill in ${...} variables; None if one of them isn't available."""
    missing = False

    def replace(match: re.Match) -> str:
        nonlocal missing
        value = variables.get(match.group(1))
        if value is None:
            missing = True
            return ""
        return value

    result = VARIABLE_RE.sub(replace, template)
    return None if missing else result


def covers(allowed: str, requested: str) -> bool:
    """Prefix-subset rule: a prefix ("…/") covers everything under it; an exact
    object location only covers itself."""
    if allowed.endswith("/"):
        return requested.startswith(allowed)
    return requested == allowed
