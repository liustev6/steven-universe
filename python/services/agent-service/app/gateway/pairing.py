"""Who is calling the gateway (C7): every request, before anything else.

- `Authorization: Bearer <capability>` must belong to a live session (else 401);
- `X-Sandbox-Id`, stamped by the pod's own egress proxy, must be that session's pod (else 403,
  logged): a capability copied to another sandbox is useless there.

Also `read_limited`: a sandbox's request body, refused past a size without reading it all.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Request

from app.sessions import Live, Sessions

logger = logging.getLogger(__name__)


def runtime_dependency(sessions: Sessions):
    """A FastAPI dependency: the calling sandbox's live session."""

    def runtime(request: Request) -> Live:
        auth = request.headers.get("authorization", "")
        capability = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        live = sessions.live_for(capability) if capability else None
        if live is None:
            raise HTTPException(401, "invalid session capability")
        stamp = request.headers.get("x-sandbox-id", "")
        if not hmac.compare_digest(stamp, str(live.sandbox_id)):
            logger.warning("session %s: its capability was used from another sandbox (%r): refused",
                           live.session_id, stamp[:64])
            raise HTTPException(403, "this capability isn't valid from this sandbox")
        return live

    return runtime


async def read_limited(request: Request, limit: int) -> bytes:
    """The body, refusing (413) past `limit` without reading the rest into memory."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "request too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "request too large")
        chunks.append(chunk)
    return b"".join(chunks)
