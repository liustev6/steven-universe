"""The HTTP API end to end: real signed tokens, the real verifier, in-memory storage."""

import hashlib

import pytest

# ---- deny by default --------------------------------------------------------------------------


def iter_routes(routes, prefix=""):
    """All (path, methods), including routers added with include_router. Newer FastAPI versions
    keep included routers as one wrapper (`original_router`) instead of copying their routes."""
    for route in routes:
        if hasattr(route, "original_router"):
            yield from iter_routes(route.original_router.routes, prefix + route.include_context.prefix)
        elif hasattr(route, "methods"):
            yield prefix + route.path, route.methods


def test_every_route_except_health_requires_a_token(client):
    """Walks the routes, so a new route without auth fails here (spec: "Deny by default")."""
    found = sorted((p, m) for p, methods in iter_routes(client.app.routes) for m in methods)
    assert found == [  # if this list changes, decide whether the new route must be authenticated
        ("/health", "GET"),
        ("/objects/{bucket}", "GET"),
        ("/objects/{bucket}/{key:path}", "DELETE"),
        ("/objects/{bucket}/{key:path}", "GET"),
        ("/objects/{bucket}/{key:path}", "HEAD"),
        ("/objects/{bucket}/{key:path}", "PUT"),
    ]
    for path, method in found:
        url = path.replace("{bucket}", "private").replace("{key:path}", "pi-server/x")
        r = client.request(method, url)
        if path == "/health":
            assert r.status_code == 200
        else:
            assert r.status_code == 401, (method, url, r.status_code)
            assert r.headers["www-authenticate"].startswith("Bearer")


def test_docs_are_off(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_bad_tokens_are_rejected(client, issuer):
    from cryptography.hazmat.primitives.asymmetric import ec

    url = "/objects/private/pi-server/x"
    wrong_aud = issuer.token("pi-server", ["files:read"], {"private/pi-server/": ["read"]}, aud="api-service")
    forged = issuer.token("pi-server", ["files:read"], {"private/pi-server/": ["read"]},
                          key=ec.generate_private_key(ec.SECP256R1()))
    for token in (wrong_aud, forged, "not-a-jwt"):
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert 'error="invalid_token"' in r.headers["www-authenticate"]


# ---- pi-server's own folder ---------------------------------------------------------------------


def test_put_get_head_list_delete_round_trip(client, pi):
    body = b"hello from the panel"
    r = client.put("/objects/private/pi-server/notes/today.txt", content=body, headers=pi)
    assert r.status_code == 200, r.text
    assert r.json()["size_bytes"] == len(body)
    assert r.json()["sha256"] == hashlib.sha256(body).hexdigest()

    r = client.get("/objects/private/pi-server/notes/today.txt", headers=pi)
    assert r.status_code == 200 and r.content == body
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["etag"] and r.headers["last-modified"] and r.headers["accept-ranges"] == "bytes"

    r = client.head("/objects/private/pi-server/notes/today.txt", headers=pi)
    assert r.status_code == 200 and r.headers["content-length"] == str(len(body))

    r = client.get("/objects/private", params={"prefix": "pi-server/notes/"}, headers=pi)
    assert r.status_code == 200
    assert [o["key"] for o in r.json()["objects"]] == ["pi-server/notes/today.txt"]

    assert client.delete("/objects/private/pi-server/notes/today.txt", headers=pi).status_code == 204
    assert client.get("/objects/private/pi-server/notes/today.txt", headers=pi).status_code == 404
    assert client.delete("/objects/private/pi-server/notes/today.txt", headers=pi).status_code == 204  # idempotent


def test_outside_its_folder_is_forbidden_not_not_found(client, pi):
    """404 only after authorization, so nobody can probe for keys they can't read."""
    for method, url in [("GET", "/objects/private/api-service/missing.txt"),
                        ("PUT", "/objects/private/api-service/x.txt"),
                        ("DELETE", "/objects/public/logo.png"),
                        ("GET", "/objects/private")]:
        r = client.request(method, url, headers=pi, content=b"x" if method == "PUT" else None)
        assert r.status_code == 403, (method, url, r.status_code)
        assert 'error="insufficient_scope"' in r.headers["www-authenticate"]
        assert r.json() == {"detail": "forbidden"}


def test_relayed_pi_server_gets_pi_servers_access(client, relayed):
    assert client.put("/objects/private/pi-server/via-edge.txt", content=b"x", headers=relayed).status_code == 200
    assert client.get("/objects/private/api-service/x", headers=relayed).status_code == 403


# ---- api-service's public token -----------------------------------------------------------------


def test_api_service_reads_public_but_cannot_write(client, api, pi, storage):
    import asyncio

    async def seed():
        async def one():
            yield b"\x89PNG..."
        await storage.put("public", "logo.png", one(), content_type="image/png", max_bytes=100)

    asyncio.run(seed())
    r = client.get("/objects/public/logo.png", headers=api)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/objects/public", headers=api).json()["objects"][0]["key"] == "logo.png"
    # write: the scope check (require) rejects it before authorize()
    r = client.put("/objects/public/logo.png", content=b"defaced", headers=api)
    assert r.status_code == 403


# ---- downloads ------------------------------------------------------------------------------------


@pytest.fixture
def stored(client, pi):
    client.put("/objects/private/pi-server/data.bin", content=b"0123456789", headers=pi)
    return "/objects/private/pi-server/data.bin"


def test_range_request(client, pi, stored):
    r = client.get(stored, headers=pi | {"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"
    assert client.get(stored, headers=pi | {"Range": "bytes=50-60"}).status_code == 416


def test_if_none_match(client, pi, stored):
    etag = client.head(stored, headers=pi).headers["etag"]
    assert client.get(stored, headers=pi | {"If-None-Match": etag}).status_code == 304
    assert client.get(stored, headers=pi | {"If-None-Match": '"other"'}).status_code == 200


# ---- validation and limits ------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "/objects/private/pi-server/%2E%2E/api-service/x",   # .. hidden from URL normalization
    "/objects/private/pi-server//x",
    "/objects/users/42/x",                               # not a bucket (yet)
    "/objects/private/",                                 # empty key
])
def test_bad_keys_get_400(client, pi, url):
    r = client.get(url, headers=pi)
    assert r.status_code == 400, (url, r.status_code)
    assert r.json() == {"detail": "invalid bucket or key"}


def test_size_limit_from_content_length(client, pi):
    r = client.put("/objects/private/pi-server/big.bin", content=b"x" * 2000, headers=pi)  # limit 1024
    assert r.status_code == 413


def test_size_limit_while_streaming(client, pi):
    def body():  # no Content-Length: chunked upload, caught on the stream
        for _ in range(5):
            yield b"x" * 400

    r = client.put("/objects/private/pi-server/big.bin", content=body(), headers=pi)
    assert r.status_code == 413
    assert client.get("/objects/private/pi-server/big.bin", headers=pi).status_code == 404


def test_list_pagination(client, pi):
    for i in range(5):
        client.put(f"/objects/private/pi-server/p/{i}.txt", content=b"x", headers=pi)
    seen, cursor = [], None
    while True:
        params = {"prefix": "pi-server/p/", "limit": 2} | ({"cursor": cursor} if cursor else {})
        page = client.get("/objects/private", params=params, headers=pi).json()
        seen += [o["key"] for o in page["objects"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [f"pi-server/p/{i}.txt" for i in range(5)]


def test_health_is_open_and_generic(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "healthy", "storage": "ok"}
