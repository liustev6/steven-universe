"""Helpers for building RFC 9396 authorization details."""

from __future__ import annotations

from typing import Iterable


def file_access(
    actions: Iterable[str], *locations: str, identifier: str | None = None
) -> dict:
    """A `file_access` detail for file-management, e.g.
    `file_access(["read", "list"], "workspaces/stevenai-v2/ws-7f3/", identifier="ws-7f3")`."""
    detail: dict = {"type": "file_access", "actions": list(actions), "locations": list(locations)}
    if identifier is not None:
        detail["identifier"] = identifier
    return detail
