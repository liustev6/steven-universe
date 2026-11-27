"""A read-only WebDAV view of the buckets (RFC 4918): what `rclone mount` reads for agent sessions
(docs/specs/agent-service/files-and-mounts.md F4; file-management.md "WebDAV view").

Only what a read-only mount needs:
- `PROPFIND` with `Depth: 0` or `1`: a folder's immediate children, or one file's properties
  (`files:list`, and a `list` grant covering the path; a listing shows what's in it anyway);
- `GET` and `HEAD`: the bytes, exactly as `/objects` serves them (ranges, `If-None-Match`);
- `OPTIONS`: `DAV: 1`.
Every write method (`PUT`, `DELETE`, `MKCOL`, `COPY`, `MOVE`, `PROPPATCH`, `LOCK`, `UNLOCK`) is 405.

Paths are `/dav/{bucket}/{key}`; a folder is a key prefix ending in "/". The same order as /objects:
the scope, the key, `authorize()`, then storage, so a caller never learns what's where it may not
look. `Depth: infinity` (or none, RFC 4918's default) is refused as the RFC allows
(`propfind-finite-depth`): a whole bucket in one answer is a denial of service.
"""

from __future__ import annotations

import logging
from datetime import timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, Response
from service_auth import Principal
from service_auth.fastapi_auth import ServiceAuth

from app.api.objects import object_head, read_object
from app.authz import authorize, permits, resource_name
from app.storage import NotFound, ObjectInfo

logger = logging.getLogger("file_management.dav")

READ_METHODS = "OPTIONS, PROPFIND, GET, HEAD"
WRITE_METHODS = ["PUT", "DELETE", "MKCOL", "COPY", "MOVE", "PROPPATCH", "LOCK", "UNLOCK", "POST"]
BODY_LIMIT = 64 * 1024            # a PROPFIND's request body is read and ignored (never parsed: no XML attacks)


def _href(bucket: str, key: str) -> str:
    return "/dav/" + quote(bucket) + "/" + quote(key, safe="/")


def _when(info: ObjectInfo) -> str:
    return format_datetime(info.last_modified.astimezone(timezone.utc), usegmt=True)


def _folder(bucket: str, prefix: str) -> str:
    return (f"<D:response><D:href>{escape(_href(bucket, prefix))}</D:href><D:propstat><D:prop>"
            "<D:resourcetype><D:collection/></D:resourcetype>"
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")


def _file(bucket: str, info: ObjectInfo, *, content_type: bool = False) -> str:
    props = (f"<D:resourcetype/><D:getcontentlength>{info.size}</D:getcontentlength>"
             f"<D:getlastmodified>{_when(info)}</D:getlastmodified>"
             f"<D:getetag>{escape(info.etag)}</D:getetag>")
    if content_type:
        props += f"<D:getcontenttype>{escape(info.content_type)}</D:getcontenttype>"
    return (f"<D:response><D:href>{escape(_href(bucket, info.key))}</D:href><D:propstat><D:prop>"
            f"{props}</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>")


def _multistatus(responses: list[str]) -> Response:
    body = ('<?xml version="1.0" encoding="utf-8"?>\n<D:multistatus xmlns:D="DAV:">'
            + "".join(responses) + "</D:multistatus>")
    return Response(body, status_code=207, media_type="application/xml; charset=utf-8")


def _finite_depth() -> Response:
    body = ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:error xmlns:D="DAV:"><D:propfind-finite-depth/></D:error>')
    return Response(body, status_code=403, media_type="application/xml; charset=utf-8")


def build_dav_router(auth: ServiceAuth) -> APIRouter:
    router = APIRouter(prefix="/dav")

    async def children(request: Request, bucket: str, prefix: str) -> list[str]:
        """A folder's immediate children, every page (PROPFIND has no paging), up to DAV_MAX_ENTRIES."""
        state = request.app.state
        out: list[str] = []
        cursor = None
        while True:
            page = await state.storage.list_dir(bucket, prefix, cursor=cursor, limit=state.settings.LIST_MAX_KEYS)
            out += [_folder(bucket, p) for p in page.prefixes]
            out += [_file(bucket, o) for o in page.objects if o.key != prefix]   # a folder marker isn't a child
            if page.next_cursor is None:
                return out
            if len(out) >= state.settings.DAV_MAX_ENTRIES:
                logger.warning("PROPFIND %s/%s: over %d entries, the rest left out",
                               bucket, prefix, state.settings.DAV_MAX_ENTRIES)
                return out
            cursor = page.next_cursor

    async def propfind(request: Request, p: Principal, bucket: str, key: str) -> Response:
        depth = request.headers.get("depth", "infinity").strip().lower()
        if depth not in ("0", "1"):
            return _finite_depth()
        body = 0
        async for chunk in request.stream():                       # drained, never parsed
            body += len(chunk)
            if body > BODY_LIMIT:
                return Response(status_code=413)
        state = request.app.state
        resource = resource_name(bucket, key, state.settings.BUCKETS, prefix=True)
        if key == "" or key.endswith("/"):
            authorize(p, "list", resource)
            folder = key
        else:
            # `notes`: a file under a granted folder, or a folder named without its slash (maybe the
            # granted folder itself). Ask the question the grant can answer; that one is audited.
            as_file = permits(p, "list", resource)
            authorize(p, "list", resource if as_file else resource + "/")
            if as_file:
                try:
                    info = await state.storage.head(bucket, key)
                    return _multistatus([_file(bucket, info, content_type=True)])
                except NotFound:
                    pass
            folder = key + "/"
            page = await state.storage.list_dir(bucket, folder, cursor=None, limit=1)
            if not (page.objects or page.prefixes):
                raise NotFound()
        found = [_folder(bucket, folder)]
        if depth == "1":
            found += await children(request, bucket, folder)
        return _multistatus(found)

    def options() -> Response:
        return Response(status_code=200, headers={"DAV": "1", "Allow": READ_METHODS, "MS-Author-Via": "DAV"})

    def read_only() -> Response:
        return Response(status_code=405, headers={"Allow": READ_METHODS})

    for path in ("/{bucket}", "/{bucket}/{key:path}"):
        @router.api_route(path, methods=["PROPFIND"])
        async def propfind_route(bucket: str, request: Request, key: str = "",
                                 p: Principal = Depends(auth.require("files:list"))):
            return await propfind(request, p, bucket, key)

        @router.api_route(path, methods=["OPTIONS"])
        async def options_route(bucket: str, key: str = "", p: Principal = Depends(auth.require())):
            return options()

        @router.api_route(path, methods=WRITE_METHODS)
        async def write_route(bucket: str, key: str = "", p: Principal = Depends(auth.require())):
            return read_only()

    @router.get("/{bucket}/{key:path}")
    async def get_route(bucket: str, key: str, request: Request,
                        p: Principal = Depends(auth.require("files:read"))):
        return await read_object(request, p, bucket, key)

    @router.head("/{bucket}/{key:path}")
    async def head_route(bucket: str, key: str, request: Request,
                         p: Principal = Depends(auth.require("files:read"))):
        return await object_head(request, p, bucket, key)

    return router
