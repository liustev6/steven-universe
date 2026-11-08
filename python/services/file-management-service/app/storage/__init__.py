"""Storage interface. The API talks only to `Storage`; `S3Storage` (RustFS) and `MemoryStorage`
(tests, local development) implement it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    etag: str
    last_modified: datetime
    content_type: str = "application/octet-stream"


@dataclass
class Download:
    info: ObjectInfo
    body: AsyncIterator[bytes]
    length: int                      # bytes in this response (the range, or the whole object)
    content_range: str | None = None  # set for 206 Partial Content


@dataclass(frozen=True)
class UploadResult:
    key: str
    size: int
    sha256: str
    etag: str


@dataclass(frozen=True)
class ListPage:
    objects: list[ObjectInfo] = field(default_factory=list)
    next_cursor: str | None = None


class StorageError(Exception):
    """Storage failed or is unreachable (502). Details are logged, never returned."""


class NotFound(StorageError):
    pass


class NotModified(StorageError):
    pass


class RangeNotSatisfiable(StorageError):
    pass


class TooLarge(StorageError):
    pass


class Storage(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def check(self, buckets: list[str]) -> bool: ...

    async def put(
        self, bucket: str, key: str, chunks: AsyncIterator[bytes], *, content_type: str, max_bytes: int
    ) -> UploadResult: ...

    async def get(
        self, bucket: str, key: str, *, byte_range: str | None = None, if_none_match: str | None = None
    ) -> Download: ...

    async def head(self, bucket: str, key: str) -> ObjectInfo: ...
    async def delete(self, bucket: str, key: str) -> None: ...
    async def list(self, bucket: str, prefix: str, *, cursor: str | None, limit: int) -> ListPage: ...
