"""S3 storage (RustFS) with aioboto3: fully async, streaming in both directions.

Uploads: the request body is read chunk by chunk, hashed (SHA-256) and buffered into parts of
PART_SIZE_BYTES. Small objects become one PutObject; larger ones a multipart upload with one part
in memory at a time. Any failure (including going over the size limit) aborts the multipart
upload so no orphaned parts stay in storage.

Downloads: GetObject's body is streamed to the client; `Range` and `If-None-Match` are passed to
storage, which answers 206 / 304 / 416 itself.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from typing import Any, AsyncIterator

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from . import (
    Download,
    ListPage,
    NotFound,
    NotModified,
    ObjectInfo,
    RangeNotSatisfiable,
    StorageError,
    TooLarge,
    UploadResult,
)

logger = logging.getLogger(__name__)
MIN_PART = 5 * 1024 * 1024
STREAM_CHUNK = 256 * 1024


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _status(exc: ClientError) -> int:
    return int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))


def _translate(exc: Exception, what: str) -> StorageError:
    if isinstance(exc, ClientError):
        code, status = _error_code(exc), _status(exc)
        if code in ("NoSuchKey", "404", "NotFound") or status == 404:
            return NotFound(what)
        if code == "304" or status == 304:
            return NotModified(what)
        if code == "InvalidRange" or status == 416:
            return RangeNotSatisfiable(what)
    logger.error("storage error during %s: %r", what, exc)
    return StorageError(what)


class S3Storage:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        region: str = "us-east-1",
        part_size: int = 8 * 1024 * 1024,
        timeout: float = 30,
    ):
        if part_size < MIN_PART:
            raise ValueError("part size must be at least 5 MiB (S3 minimum)")
        self._session = aioboto3.Session(
            aws_access_key_id=access_key, aws_secret_access_key=secret_key, region_name=region
        )
        self._endpoint = endpoint
        self._config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        self._part_size = part_size
        self._stack = contextlib.AsyncExitStack()
        self._s3: Any = None

    async def start(self) -> None:
        self._s3 = await self._stack.enter_async_context(
            self._session.client("s3", endpoint_url=self._endpoint, config=self._config)
        )

    async def close(self) -> None:
        await self._stack.aclose()

    async def check(self, buckets: list[str]) -> bool:
        try:
            for bucket in buckets:
                await self._s3.head_bucket(Bucket=bucket)
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.error("storage check failed: %r", exc)
            return False

    # ---- upload ---------------------------------------------------------------------------
    async def put(self, bucket, key, chunks: AsyncIterator[bytes], *, content_type, max_bytes):
        sha = hashlib.sha256()
        size = 0
        buf = bytearray()
        upload_id: str | None = None
        parts: list[dict[str, Any]] = []

        async def send_part(data: bytes) -> None:
            nonlocal upload_id
            if upload_id is None:
                created = await self._s3.create_multipart_upload(
                    Bucket=bucket, Key=key, ContentType=content_type
                )
                upload_id = created["UploadId"]
            number = len(parts) + 1
            resp = await self._s3.upload_part(
                Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=data
            )
            parts.append({"PartNumber": number, "ETag": resp["ETag"]})

        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge(f"over {max_bytes} bytes")
                sha.update(chunk)
                buf += chunk
                while len(buf) >= self._part_size:
                    await send_part(bytes(buf[: self._part_size]))
                    del buf[: self._part_size]

            if upload_id is None:
                resp = await self._s3.put_object(
                    Bucket=bucket, Key=key, Body=bytes(buf), ContentType=content_type
                )
                etag = resp["ETag"]
            else:
                if buf:
                    await send_part(bytes(buf))
                resp = await self._s3.complete_multipart_upload(
                    Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
                )
                etag = resp["ETag"]
        except BaseException as exc:
            if upload_id is not None:
                with contextlib.suppress(Exception):
                    await self._s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            if isinstance(exc, (TooLarge, StorageError)) or not isinstance(exc, Exception):
                raise
            if isinstance(exc, (ClientError, BotoCoreError)):
                raise _translate(exc, f"put {bucket}/{key}") from None
            raise
        return UploadResult(key, size, sha.hexdigest(), etag)

    # ---- read -----------------------------------------------------------------------------
    async def get(self, bucket, key, *, byte_range=None, if_none_match=None):
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if byte_range:
            kwargs["Range"] = byte_range
        if if_none_match:
            kwargs["IfNoneMatch"] = if_none_match
        try:
            resp = await self._s3.get_object(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, f"get {bucket}/{key}") from None
        body = resp["Body"]

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in body.iter_chunks(STREAM_CHUNK):
                    yield chunk
            finally:
                body.close()

        info = ObjectInfo(
            key=key,
            size=_total_size(resp),
            etag=resp["ETag"],
            last_modified=resp["LastModified"],
            content_type=resp.get("ContentType") or "application/octet-stream",
        )
        return Download(info, stream(), int(resp["ContentLength"]), resp.get("ContentRange"))

    async def head(self, bucket, key):
        try:
            resp = await self._s3.head_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, f"head {bucket}/{key}") from None
        return ObjectInfo(
            key=key,
            size=int(resp["ContentLength"]),
            etag=resp["ETag"],
            last_modified=resp["LastModified"],
            content_type=resp.get("ContentType") or "application/octet-stream",
        )

    async def delete(self, bucket, key):
        try:
            await self._s3.delete_object(Bucket=bucket, Key=key)  # S3 delete is idempotent
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, f"delete {bucket}/{key}") from None

    async def list(self, bucket, prefix, *, cursor, limit):
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": limit}
        if cursor:
            kwargs["ContinuationToken"] = cursor
        try:
            resp = await self._s3.list_objects_v2(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, f"list {bucket}/{prefix}") from None
        objects = [
            ObjectInfo(key=o["Key"], size=int(o["Size"]), etag=o["ETag"], last_modified=o["LastModified"])
            for o in resp.get("Contents", [])
        ]
        return ListPage(objects, resp.get("NextContinuationToken") if resp.get("IsTruncated") else None)


def _total_size(resp: dict[str, Any]) -> int:
    content_range = resp.get("ContentRange")  # "bytes 0-3/1234" on a range request
    if content_range and "/" in content_range:
        total = content_range.rsplit("/", 1)[1]
        if total.isdigit():
            return int(total)
    return int(resp["ContentLength"])
