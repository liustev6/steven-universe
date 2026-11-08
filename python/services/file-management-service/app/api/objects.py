"""The object API (spec "Target API"). Every route follows the same order:

1. `auth.require("files:<action>")`: a valid token for file-management with that scope (401/403)
2. `resource_name()`: the bucket is known and the key is clean (400)
3. `authorize()`: a grant covers this action on this resource (403)
4. storage, which may answer 404/304/416

Because 3 comes before 4, a caller can't learn whether a key exists where it may not read.
"""

from __future__ import annotations

from datetime import timezone
from email.utils import format_datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from service_auth import Principal
from service_auth.fastapi_auth import ServiceAuth

from app.authz import authorize, resource_name
from app.content_type import detect_content_type
from app.storage import ObjectInfo


def _object_headers(info: ObjectInfo) -> dict[str, str]:
    # botocore tags times with dateutil's tzutc(); format_datetime(usegmt=True) only accepts the
    # standard library's timezone.utc, so normalize first.
    last_modified = info.last_modified.astimezone(timezone.utc)
    return {
        "ETag": info.etag,
        "Last-Modified": format_datetime(last_modified, usegmt=True),
        "Accept-Ranges": "bytes",
    }


def build_router(auth: ServiceAuth) -> APIRouter:
    router = APIRouter(prefix="/objects")

    @router.put("/{bucket}/{key:path}")
    async def put_object(
        bucket: str, key: str, request: Request, p: Principal = Depends(auth.require("files:write"))
    ):
        state = request.app.state
        authorize(p, "write", resource_name(bucket, key, state.settings.BUCKETS))
        limit = state.settings.max_upload_bytes(bucket)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "too large")
        result = await state.storage.put(
            bucket,
            key,
            request.stream(),
            content_type=detect_content_type(key, request.headers.get("content-type")),
            max_bytes=limit,
        )
        return {
            "bucket": bucket,
            "key": result.key,
            "size_bytes": result.size,
            "sha256": result.sha256,
            "etag": result.etag,
        }

    @router.get("/{bucket}/{key:path}")
    async def get_object(
        bucket: str, key: str, request: Request, p: Principal = Depends(auth.require("files:read"))
    ):
        state = request.app.state
        authorize(p, "read", resource_name(bucket, key, state.settings.BUCKETS))
        download = await state.storage.get(
            bucket,
            key,
            byte_range=request.headers.get("range"),
            if_none_match=request.headers.get("if-none-match"),
        )
        headers = _object_headers(download.info) | {"Content-Length": str(download.length)}
        if download.content_range:
            headers["Content-Range"] = download.content_range
        return StreamingResponse(
            download.body,
            status_code=206 if download.content_range else 200,
            media_type=download.info.content_type,
            headers=headers,
        )

    @router.head("/{bucket}/{key:path}")
    async def head_object(
        bucket: str, key: str, request: Request, p: Principal = Depends(auth.require("files:read"))
    ):
        state = request.app.state
        authorize(p, "read", resource_name(bucket, key, state.settings.BUCKETS))
        info = await state.storage.head(bucket, key)
        headers = _object_headers(info) | {
            "Content-Length": str(info.size),
            "Content-Type": info.content_type,
        }
        return Response(status_code=200, headers=headers)

    @router.delete("/{bucket}/{key:path}", status_code=204)
    async def delete_object(
        bucket: str, key: str, request: Request, p: Principal = Depends(auth.require("files:delete"))
    ):
        state = request.app.state
        authorize(p, "delete", resource_name(bucket, key, state.settings.BUCKETS))
        await state.storage.delete(bucket, key)  # idempotent: 204 even if it didn't exist
        return Response(status_code=204)

    @router.get("/{bucket}")
    async def list_objects(
        bucket: str,
        request: Request,
        prefix: str = "",
        cursor: str | None = None,
        limit: int = Query(1000, ge=1),
        p: Principal = Depends(auth.require("files:list")),
    ):
        state = request.app.state
        # The requested prefix itself must be inside a granted location (no listing a parent).
        authorize(p, "list", resource_name(bucket, prefix, state.settings.BUCKETS, prefix=True))
        page = await state.storage.list(
            bucket, prefix, cursor=cursor, limit=min(limit, state.settings.LIST_MAX_KEYS)
        )
        return {
            "bucket": bucket,
            "prefix": prefix,
            "objects": [
                {
                    "key": o.key,
                    "size_bytes": o.size,
                    "etag": o.etag,
                    "last_modified": o.last_modified.isoformat(),
                }
                for o in page.objects
            ],
            "next_cursor": page.next_cursor,
        }

    return router
