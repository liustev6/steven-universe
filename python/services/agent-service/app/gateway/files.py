"""/files: `sfsync`'s writes, checked here (files-and-mounts.md F7, F10, F12).

    PUT    /files/<bucket>/<key>    sfsync put: create or replace; the body is the file's bytes
    DELETE /files/<bucket>/<key>    sfsync rm

Paired like every gateway route (C7: the session's capability, from its own pod). The gateway decides
every action; `sfsync` checks nothing itself:
- a turn is running (its caller's token is what gets exchanged; between turns nothing is written);
- the session's grant (`live.files`, fixed at its start, after the intersection) allows `write` (PUT)
  or `delete` (DELETE) on a folder holding the destination;
- a PUT is within the profile's `limits.max_file_mb`.
Then file-management's `/objects`, with a token exchanged for the turn's caller asking for exactly the
session's grant; file-management checks it again and audits *agent-service on behalf of the caller*.
Each change becomes `artifact.created` / `artifact.deleted` in the session's events (protocol B).

Errors are RFC 9457 problem details (`application/problem+json`): `title`, `status`, a `detail`
sentence the agent can act on, and `allowed` (the folders it may use) when the destination is refused.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.file_management import NoFiles, covers, locations
from app.protocol import events as b
from app.sessions import Live, Sessions

logger = logging.getLogger(__name__)
PROBLEM = "application/problem+json"
TITLES = {400: "Bad Request", 403: "Forbidden", 404: "Not Found", 409: "Conflict", 413: "Content Too Large",
          502: "Bad Gateway", 503: "Service Unavailable"}
SOURCE_HEADER = "x-sfsync-source"          # the local file it came from, for artifact.created (informational)


def problem(status: int, detail: str, **extra: Any) -> JSONResponse:
    """An RFC 9457 problem: `type` is about:blank, so `title` is the status's own phrase."""
    body = {"type": "about:blank", "title": TITLES.get(status, "Error"), "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def destination(raw_path: str) -> tuple[str | None, str | None]:
    """(`<bucket>/<key>`, None) for a well-formed destination, else (None, why)."""
    location = unquote(raw_path[len("/files"):].lstrip("/"))
    segments = location.split("/")
    if any(seg in (".", "..") for seg in segments) or "//" in location or "\\" in location:
        return None, f"{location!r} is not a valid destination ('.', '..' and empty folder names aren't allowed)"
    if len(segments) < 2 or not segments[0] or location.endswith("/"):
        return None, f"{location!r} is not a file: give <bucket>/<folders…>/<file name>"
    return location, None


def build_router(sessions: Sessions, paired) -> APIRouter:
    router = APIRouter()

    def refused(live: Live, action: str, verb: str, location: str) -> JSONResponse | None:
        """None when the session may `action` at `location`; else the problem to answer."""
        if covers(live.files, action, location):
            return None
        allowed = locations(live.files, action)
        if not allowed:
            return problem(403, f"this session may not {verb} any file", allowed=[])
        return problem(403, f"{location} is outside the folders you may {verb} ({', '.join(allowed)})",
                       allowed=allowed)

    def running(live: Live) -> JSONResponse | None:
        if sessions.files is None:
            return problem(503, "file storage isn't configured here; nothing was changed")
        if live.caller_token is None:
            return problem(409, "no turn is running; nothing was changed")
        return None

    def upstream_problem(r: httpx.Response, location: str) -> JSONResponse:
        """file-management's refusal, in words for the agent."""
        if r.status_code == 413:
            return problem(413, f"{location}: storage refused the file as too large")
        if r.status_code in (400, 422):
            return problem(400, f"{location}: storage refused the name (letters, digits, '.', '_', '-' and "
                                "'/' between folders are safe)")
        if r.status_code in (401, 403):
            return problem(403, f"{location}: storage refused it (the session's grant doesn't cover it)")
        return problem(502, f"storage answered {r.status_code}; nothing was changed, try again")

    @router.put("/files/{path:path}")
    async def put(request: Request, live: Live = Depends(paired)) -> Response:
        location, why = destination(request.scope.get("raw_path", b"").decode("latin-1").split("?", 1)[0])
        if location is None:
            return problem(400, why)
        if (answer := refused(live, "write", "write to", location)) or (answer := running(live)):
            return answer
        limit = sessions.profiles[live.profile].limits.max_file_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return problem(413, f"{int(declared) / 1048576:.1f} MB is over this session's limit of "
                                f"{limit // 1048576} MB per file", limit_bytes=limit)
        body, digest = bytearray(), hashlib.sha256()
        async for chunk in request.stream():
            body += chunk
            digest.update(chunk)
            if len(body) > limit:
                return problem(413, f"the file is over this session's limit of {limit // 1048576} MB per file",
                               limit_bytes=limit)
        try:
            r = await sessions.files.put(live.caller_token, live.files, location, bytes(body),
                                         request.headers.get("content-type"))
        except NoFiles:
            return problem(403, f"{location}: the caller's grant no longer covers it")
        except httpx.HTTPError as e:
            logger.warning("session %s: file-management unreachable: %s", live.session_id, e)
            return problem(503, "storage did not answer; nothing was changed, try again")
        if r.status_code >= 300:
            logger.info("session %s: PUT %s: file-management answered %d", live.session_id, location, r.status_code)
            return upstream_problem(r, location)
        size, sha = len(body), digest.hexdigest()
        source = unquote(request.headers.get(SOURCE_HEADER, "")) or None
        await sessions.log.append(live.session_id, [b.new_event(
            b.ArtifactCreated(location=location, source=source, size=size, sha256=sha), turn_id=live.turn_id)])
        logger.info("session %s: saved %s (%d bytes)", live.session_id, location, size)
        return JSONResponse({"location": location, "size": size, "sha256": sha}, status_code=201)

    @router.delete("/files/{path:path}")
    async def delete(request: Request, live: Live = Depends(paired)) -> Response:
        location, why = destination(request.scope.get("raw_path", b"").decode("latin-1").split("?", 1)[0])
        if location is None:
            return problem(400, why)
        if (answer := refused(live, "delete", "delete in", location)) or (answer := running(live)):
            return answer
        try:
            # Told apart only when the session may read there: file-management's delete is idempotent.
            if covers(live.files, "read", location) and await sessions.files.exists(
                    live.caller_token, live.files, location) is False:
                return problem(404, f"{location} isn't there; nothing was removed")
            r = await sessions.files.delete(live.caller_token, live.files, location)
        except NoFiles:
            return problem(403, f"{location}: the caller's grant no longer covers it")
        except httpx.HTTPError as e:
            logger.warning("session %s: file-management unreachable: %s", live.session_id, e)
            return problem(503, "storage did not answer; nothing was changed, try again")
        if r.status_code >= 300:
            logger.info("session %s: DELETE %s: file-management answered %d", live.session_id, location, r.status_code)
            return upstream_problem(r, location)
        await sessions.log.append(live.session_id, [b.new_event(b.ArtifactDeleted(location=location),
                                                                turn_id=live.turn_id)])
        logger.info("session %s: removed %s", live.session_id, location)
        return Response(status_code=204)

    return router
