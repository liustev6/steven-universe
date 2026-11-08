"""Content-Type for uploads: the client's type unless it's generic, else a guess from the key."""

from __future__ import annotations

import mimetypes

GENERIC = "application/octet-stream"


def detect_content_type(key: str, provided: str | None = None) -> str:
    provided = (provided or "").split(";")[0].strip().lower() or None
    if provided and provided != GENERIC and "/" in provided:
        return provided
    guessed, _ = mimetypes.guess_type(key)
    return guessed or GENERIC
