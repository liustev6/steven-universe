"""In-memory storage for tests and local development (FILES_STORAGE=memory with ENV=local)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import AsyncIterator

from . import (
    Download,
    ListPage,
    NotFound,
    NotModified,
    ObjectInfo,
    RangeNotSatisfiable,
    TooLarge,
    UploadResult,
)

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class MemoryStorage:
    def __init__(self, buckets: list[str]):
        self.objects: dict[str, dict[str, tuple[bytes, ObjectInfo]]] = {b: {} for b in buckets}

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def check(self, buckets: list[str]) -> bool:
        return all(b in self.objects for b in buckets)

    async def put(self, bucket, key, chunks: AsyncIterator[bytes], *, content_type, max_bytes):
        data = bytearray()
        async for chunk in chunks:
            data += chunk
            if len(data) > max_bytes:
                raise TooLarge(f"over {max_bytes} bytes")
        body = bytes(data)
        sha = hashlib.sha256(body).hexdigest()
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        info = ObjectInfo(key, len(body), etag, datetime.now(timezone.utc), content_type)
        self.objects[bucket][key] = (body, info)
        return UploadResult(key, len(body), sha, etag)

    async def get(self, bucket, key, *, byte_range=None, if_none_match=None):
        body, info = self._find(bucket, key)
        if if_none_match and if_none_match == info.etag:
            raise NotModified()
        start, end, content_range = 0, len(body) - 1, None
        if byte_range:
            m = _RANGE.match(byte_range)
            if not m or (not m[1] and not m[2]):
                raise RangeNotSatisfiable()
            if m[1]:
                start, end = int(m[1]), min(int(m[2]) if m[2] else len(body) - 1, len(body) - 1)
            else:  # suffix range: the last N bytes
                start = max(len(body) - int(m[2]), 0)
            if start > end or start >= len(body):
                raise RangeNotSatisfiable()
            content_range = f"bytes {start}-{end}/{len(body)}"
        part = body[start : end + 1]

        async def stream():
            yield part

        return Download(info, stream(), len(part), content_range)

    async def head(self, bucket, key):
        return self._find(bucket, key)[1]

    async def delete(self, bucket, key):
        self.objects[bucket].pop(key, None)

    async def list(self, bucket, prefix, *, cursor, limit):
        keys = sorted(k for k in self.objects[bucket] if k.startswith(prefix) and (cursor is None or k > cursor))
        page = keys[:limit]
        next_cursor = page[-1] if len(keys) > limit else None
        return ListPage([self.objects[bucket][k][1] for k in page], next_cursor)

    def _find(self, bucket, key):
        try:
            return self.objects[bucket][key]
        except KeyError:
            raise NotFound() from None
