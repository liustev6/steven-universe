"""S3Storage against a local S3 server (moto), so the real aioboto3 code paths run:
single PUT, multipart, abort on over-size, ranges, If-None-Match, pagination, error mapping."""

import asyncio
import hashlib

import boto3
import pytest
from moto.server import ThreadedMotoServer

from app.storage import NotFound, NotModified, RangeNotSatisfiable, TooLarge
from app.storage.s3 import S3Storage

MiB = 1024 * 1024


@pytest.fixture(scope="module")
def endpoint():
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    host, port = server.get_host_and_port()
    url = f"http://{host}:{port}"
    s3 = boto3.client("s3", endpoint_url=url, aws_access_key_id="test", aws_secret_access_key="test",
                      region_name="us-east-1")
    for bucket in ("public", "private"):
        s3.create_bucket(Bucket=bucket)
    yield url, s3
    server.stop()


def run(endpoint, scenario):
    """Run `scenario(storage)` against a started S3Storage (5 MiB parts, the S3 minimum)."""
    url, _ = endpoint

    async def main():
        storage = S3Storage(url, "test", "test", part_size=5 * MiB)
        await storage.start()
        try:
            return await scenario(storage)
        finally:
            await storage.close()

    return asyncio.run(main())


def chunks(data: bytes, size: int = 256 * 1024):
    async def gen():
        for i in range(0, len(data), size):
            yield data[i : i + size]
    return gen()


async def read_all(download) -> bytes:
    return b"".join([c async for c in download.body])


def test_small_put_and_get(endpoint):
    async def scenario(st):
        res = await st.put("private", "pi-server/a.txt", chunks(b"hello"), content_type="text/plain", max_bytes=MiB)
        assert res.size == 5 and res.sha256 == hashlib.sha256(b"hello").hexdigest()
        d = await st.get("private", "pi-server/a.txt")
        assert await read_all(d) == b"hello"
        assert d.info.content_type == "text/plain" and d.info.size == 5 and d.length == 5
        assert await st.check(["public", "private"]) is True
        assert await st.check(["nope"]) is False
    run(endpoint, scenario)


def test_multipart_upload(endpoint):
    data = bytes(range(256)) * (12 * MiB // 256)   # 12 MiB: parts of 5 + 5 + 2 MiB

    async def scenario(st):
        res = await st.put("private", "pi-server/big.bin", chunks(data), content_type="application/octet-stream",
                           max_bytes=50 * MiB)
        assert res.size == len(data) and res.sha256 == hashlib.sha256(data).hexdigest()
        assert res.etag.strip('"').endswith("-3")   # S3's multipart ETag: md5-of-parts "-<count>"
        assert await read_all(await st.get("private", "pi-server/big.bin")) == data
    run(endpoint, scenario)


def test_too_large_aborts_the_multipart_upload(endpoint):
    _, s3 = endpoint

    async def scenario(st):
        with pytest.raises(TooLarge):
            await st.put("private", "pi-server/huge.bin", chunks(b"x" * (11 * MiB)),
                         content_type="application/octet-stream", max_bytes=10 * MiB)
        with pytest.raises(NotFound):
            await st.head("private", "pi-server/huge.bin")
    run(endpoint, scenario)
    assert s3.list_multipart_uploads(Bucket="private").get("Uploads", []) == []  # no orphaned parts


def test_range_and_conditional_get(endpoint):
    async def scenario(st):
        await st.put("public", "digits.txt", chunks(b"0123456789"), content_type="text/plain", max_bytes=MiB)
        d = await st.get("public", "digits.txt", byte_range="bytes=2-5")
        assert await read_all(d) == b"2345"
        assert d.content_range == "bytes 2-5/10" and d.length == 4 and d.info.size == 10
        etag = (await st.head("public", "digits.txt")).etag
        with pytest.raises(NotModified):
            await st.get("public", "digits.txt", if_none_match=etag)
        with pytest.raises(RangeNotSatisfiable):
            await st.get("public", "digits.txt", byte_range="bytes=50-60")
    run(endpoint, scenario)


def test_list_pagination_and_delete(endpoint):
    async def scenario(st):
        for i in range(5):
            await st.put("private", f"pi-server/list/{i}.txt", chunks(b"x"), content_type="text/plain", max_bytes=MiB)
        await st.put("private", "api-service/other.txt", chunks(b"x"), content_type="text/plain", max_bytes=MiB)
        seen, cursor = [], None
        while True:
            page = await st.list("private", "pi-server/list/", cursor=cursor, limit=2)
            seen += [o.key for o in page.objects]
            cursor = page.next_cursor
            if not cursor:
                break
        assert seen == [f"pi-server/list/{i}.txt" for i in range(5)]
        await st.delete("private", "pi-server/list/0.txt")
        await st.delete("private", "pi-server/list/0.txt")      # idempotent
        with pytest.raises(NotFound):
            await st.get("private", "pi-server/list/0.txt")
    run(endpoint, scenario)


def test_http_api_on_s3_storage(endpoint, issuer, pi):
    """The whole stack: HTTP -> real token check -> authorize -> S3Storage -> moto, streaming both ways."""
    import httpx
    from fastapi.testclient import TestClient
    from service_auth.fastapi_auth import ServiceAuth
    from service_auth.verifier import JWKSCache, TokenVerifier

    from app.config import Settings
    from app.main import create_app
    from tests.conftest import ISSUER

    url, _ = endpoint
    settings = Settings(MAX_UPLOAD_BYTES=50 * MiB, PART_SIZE_BYTES=5 * MiB)
    http = httpx.AsyncClient(transport=httpx.MockTransport(issuer.handler))
    auth = ServiceAuth(TokenVerifier(issuer=ISSUER, audience="file-management", jwks=JWKSCache(ISSUER, http=http)))
    data = b"panel-snapshot" * (600 * 1024)   # ~8.4 MiB: a multipart upload through HTTP
    with TestClient(create_app(settings, storage=S3Storage(url, "test", "test", part_size=5 * MiB), auth=auth)) as c:
        r = c.put("/objects/private/pi-server/snap.bin", content=data, headers=pi)
        assert r.status_code == 200 and r.json()["sha256"] == hashlib.sha256(data).hexdigest()
        r = c.get("/objects/private/pi-server/snap.bin", headers=pi)
        assert r.status_code == 200 and r.content == data
        r = c.get("/objects/private/pi-server/snap.bin", headers=pi | {"Range": "bytes=0-13"})
        assert r.status_code == 206 and r.content == b"panel-snapshot"
        assert c.get("/objects/private/pi-server/missing", headers=pi).status_code == 404
        assert c.get("/health").json() == {"status": "healthy", "storage": "ok"}
