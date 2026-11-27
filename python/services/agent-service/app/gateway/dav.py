"""/dav: the session's files, read-only, for its pod's mounter (files-and-mounts.md F2, F4, F5).

Every mounter mounts this same /dav; what it shows depends on whose capability it carries. Each request
is paired first (C7: the session's capability, from its own pod), then by path:

- **inside a folder the session may read** (`live.files`, as granted at its start): forwarded to
  file-management's /dav, the path exactly as it came, with a token exchanged for the running turn's
  caller (RFC 8693), asking for exactly the session's grant. Between turns there is no caller token,
  so nothing is read (403);
- **above those folders** (`/dav/`, `/dav/public/` when the grant is `public/steven-universe/`): a
  listing made here of just the folders that lead down to them; nothing else of the bucket is shown,
  and file-management isn't asked;
- **anything else:** 404, as if it didn't exist.

Paths are file-management's: `/dav/<bucket>/<key>`, so its answers (hrefs included) pass through as they
are. Only PROPFIND (Depth 0 or 1), GET, HEAD and OPTIONS; every write method is 405 (writes are
`publish`, F7).
"""

from __future__ import annotations

import logging
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from xml.sax.saxutils import escape

from app.file_management import NoFiles, locations
from app.sessions import Live, Sessions

logger = logging.getLogger(__name__)
READ_METHODS = "OPTIONS, PROPFIND, GET, HEAD"
WRITE_METHODS = ["PUT", "DELETE", "MKCOL", "COPY", "MOVE", "PROPPATCH", "LOCK", "UNLOCK", "POST"]
BODY_LIMIT = 64 * 1024
PASSED_BACK = ("content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified")


def _folder(href: str) -> str:
    return (f"<D:response><D:href>{escape(href)}</D:href><D:propstat><D:prop>"
            "<D:resourcetype><D:collection/></D:resourcetype>"
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")


def _multistatus(responses: list[str]) -> Response:
    body = '<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">' + "".join(responses) + "</D:multistatus>"
    return Response(body, status_code=207, media_type="application/xml; charset=utf-8")


def _finite_depth() -> Response:
    body = '<?xml version="1.0" encoding="utf-8"?>\n<D:error xmlns:D="DAV:"><D:propfind-finite-depth/></D:error>'
    return Response(body, status_code=403, media_type="application/xml; charset=utf-8")


def classify(path: str, readable: list[str]) -> tuple[str, list[str]]:
    """("inside", []) | ("above", [child folders]) | ("outside", []) for a decoded path (no leading /)."""
    as_folder = path if path == "" or path.endswith("/") else path + "/"
    if any(path.startswith(loc) or as_folder == loc for loc in readable):
        return "inside", []
    children = sorted({loc[len(as_folder):].split("/", 1)[0] for loc in readable
                       if loc.startswith(as_folder) and loc != as_folder})
    if as_folder == "" or children:                           # the root always answers (maybe empty)
        return "above", children
    return "outside", []


def build_router(sessions: Sessions, paired) -> APIRouter:
    router = APIRouter()

    async def forward(request: Request, live: Live, raw: str) -> Response:
        if sessions.files is None:
            return Response(status_code=503)
        if live.caller_token is None:                           # between turns: no caller, no new token
            return Response("no turn is running", status_code=403)
        try:
            upstream = await sessions.files.dav(live.caller_token, live.files, request.method, raw,
                                                dict(request.headers))
        except NoFiles:
            return Response(status_code=403)
        except httpx.HTTPError as e:
            logger.warning("session %s: file-management unreachable: %s", live.session_id, e)
            return Response(status_code=502)
        if upstream.status_code >= 400:
            logger.info("session %s: %s /dav/%s: file-management answered %d", live.session_id, request.method,
                        unquote(raw)[:200], upstream.status_code)
        headers = {k: v for k, v in upstream.headers.items() if k.lower() in PASSED_BACK}
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers,
                                 background=BackgroundTask(upstream.aclose))

    async def handle(request: Request, live: Live) -> Response:
        raw = request.scope.get("raw_path", b"").decode("latin-1").split("?", 1)[0]
        raw = raw[len("/dav"):].lstrip("/")                     # file-management's path, still encoded
        path = unquote(raw)
        if any(seg in (".", "..") for seg in path.split("/")) or "//" in path or "\\" in path:
            return Response(status_code=400)
        if request.method in WRITE_METHODS:
            return Response(status_code=405, headers={"Allow": READ_METHODS})
        if request.method == "OPTIONS":
            return Response(status_code=200, headers={"DAV": "1", "Allow": READ_METHODS})
        kind, children = classify(path, locations(live.files, "read", "list"))
        if kind == "inside":
            return await forward(request, live, raw)
        if kind == "outside" or request.method != "PROPFIND":
            return Response(status_code=404)
        depth = request.headers.get("depth", "infinity").strip().lower()
        if depth not in ("0", "1"):
            return _finite_depth()
        size = 0
        async for chunk in request.stream():                    # drained, never parsed
            size += len(chunk)
            if size > BODY_LIMIT:
                return Response(status_code=413)
        here = "/dav/" + quote(path if path == "" or path.endswith("/") else path + "/", safe="/")
        found = [_folder(here)]
        if depth == "1":
            found += [_folder(here + quote(child, safe="") + "/") for child in children]
        return _multistatus(found)

    async def dav(request: Request, live: Live = Depends(paired)) -> Response:
        return await handle(request, live)

    methods = ["PROPFIND", "GET", "HEAD", "OPTIONS", *WRITE_METHODS]
    router.add_api_route("/dav", dav, methods=methods)
    router.add_api_route("/dav/{path:path}", dav, methods=methods)
    return router
