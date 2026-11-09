"""api-service: deny-by-default route modes, the edge pipeline, and the deploy proxy."""

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import Settings
from app.edge import Mode, client_ip
from app.main import create_app
from app.ratelimit import RateLimiter

# The reviewed list of routes per mode (spec E2): a new public or proxy route must be added here.
EXPECTED = {
    ("GET", "/health"): Mode.PUBLIC,
    ("POST", "/deploy/v1/deployments"): Mode.PROXY,
}


def fake_jwt(**claims) -> str:
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    base = {"iss": "https://token.actions.githubusercontent.com", "aud": "steven-universe-deployer",
            "exp": int(time.time()) + 300}
    return f"{seg({'alg': 'RS256'})}.{seg({**base, **claims})}.c2lnbmF0dXJl"


class Deployer:
    """Plays the deployer behind the proxy."""

    def __init__(self, status=200, chunks=(b'{"event": "accepted"}\n', b'{"event": "result", "status": "succeeded"}\n'),
                 fail=False):
        self.requests: list[httpx.Request] = []
        self.status, self.chunks, self.fail = status, chunks, fail

    def transport(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.fail:
                raise httpx.ConnectError("refused")

            async def stream():
                for c in self.chunks:
                    yield c
            return httpx.Response(self.status, headers={"content-type": "application/x-ndjson"},
                                  content=stream())
        return httpx.MockTransport(handler)


@pytest.fixture
def deployer():
    return Deployer()


@pytest.fixture
def client(deployer):
    settings = Settings(DEPLOY_RATE_BURST=3, DEPLOY_RATE_PER_MINUTE=1)
    with TestClient(create_app(settings, http=httpx.AsyncClient(transport=deployer.transport()))) as c:
        yield c


def deploy(client, token="VALID", body=None):
    token = fake_jwt() if token == "VALID" else token
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/deploy/v1/deployments", headers=headers,
                       content=json.dumps(body or {"service": "file-management", "sha": "a" * 40}))


# ---- deny by default ---------------------------------------------------------------------------

def iter_routes(routes, prefix=""):
    """Every endpoint, including routers added with include_router (newer FastAPI versions keep
    an included router as one wrapper, `original_router`, instead of copying its routes)."""
    for route in routes:
        if hasattr(route, "original_router"):
            yield from iter_routes(route.original_router.routes, prefix + route.include_context.prefix)
        elif hasattr(route, "dependant"):
            yield prefix + route.path, route


def test_every_route_declares_a_mode_and_the_list_is_reviewed(client):
    found = {}
    for path, route in iter_routes(client.app.routes):
        modes = [getattr(d.call, "access_mode", None) for d in route.dependant.dependencies]
        modes = [m for m in modes if m]
        assert len(modes) == 1, f"{path} must declare exactly one access mode"
        for method in route.methods:
            found[(method, path)] = modes[0]
    assert found == EXPECTED


def test_health_says_ok_only(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert r.headers["cache-control"] == "no-store" and len(r.headers["x-request-id"]) == 32


@pytest.mark.parametrize("method, path, status", [("GET", "/docs", 404), ("GET", "/openapi.json", 404),
                                                  ("GET", "/status", 404), ("GET", "/deploy/v1/deployments", 405),
                                                  ("POST", "/deploy/v1/other", 404)])
def test_nothing_else(client, method, path, status):
    r = client.request(method, path)
    assert r.status_code == status and r.json()["error"] in ("not_found", "method_not_allowed")


# ---- the deploy proxy ----------------------------------------------------------------------------

def test_forwards_unchanged_and_streams_back(client, deployer):
    token = fake_jwt(sha="a" * 40)
    r = deploy(client, token, {"service": "file-management", "sha": "a" * 40})
    assert r.status_code == 200 and r.headers["content-type"] == "application/x-ndjson"
    assert [json.loads(line)["event"] for line in r.text.splitlines()] == ["accepted", "result"]
    sent = deployer.requests[0]
    assert str(sent.url) == "https://deploy.lan.liustev6.ca/v1/deployments"
    assert sent.headers["authorization"] == f"Bearer {token}"           # unchanged: the deployer verifies it
    assert json.loads(sent.content) == {"service": "file-management", "sha": "a" * 40}
    assert "cookie" not in sent.headers


def test_deployer_errors_pass_through(deployer, client):
    deployer.status, deployer.chunks = 403, (b'{"error": "environment is \'dev\'"}',)
    r = deploy(client)
    assert r.status_code == 403 and "environment" in r.json()["error"]


@pytest.mark.parametrize("token, reason", [
    (None, "bearer token is required"),
    ("abc", "malformed"),
    ("a.%%%.c", "malformed"),
    (fake_jwt(iss="https://accounts.google.com"), "not a GitHub Actions token"),
    (fake_jwt(aud="sts.amazonaws.com"), "not issued for the deployer"),
    (fake_jwt(exp=int(time.time()) - 600), "expired"),
])
def test_prechecks_stop_junk_at_the_edge(client, deployer, token, reason):
    r = deploy(client, token)
    assert r.status_code == 401 and reason in r.json()["message"]
    assert not deployer.requests                                         # never reached the LAN


def test_body_limit(client, deployer):
    r = client.post("/deploy/v1/deployments", headers={"Authorization": f"Bearer {fake_jwt()}"}, content=b"x" * 5000)
    assert r.status_code == 413 and not deployer.requests


def test_rate_limit(client, deployer):
    assert [deploy(client, None).status_code for _ in range(4)] == [401, 401, 401, 429]


def test_deployer_down_is_502(deployer, client):
    deployer.fail = True
    r = deploy(client)
    assert r.status_code == 502 and r.json()["message"] == "the deployer is unavailable"


# ---- client IP and rate limiter --------------------------------------------------------------------

def request_from(peer: str, cf_ip: str | None) -> Request:
    headers = [(b"cf-connecting-ip", cf_ip.encode())] if cf_ip else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_cf_connecting_ip_trusted_only_from_cloudflared():
    s = Settings()
    assert client_ip(request_from("192.168.50.53", "203.0.113.7"), s) == "203.0.113.7"
    assert client_ip(request_from("192.168.50.99", "203.0.113.7"), s) == "192.168.50.99"   # spoofed header
    assert client_ip(request_from("192.168.50.53", "not-an-ip"), s) == "192.168.50.53"


def test_token_bucket_refills():
    now = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: now[0])     # 1 per second
    assert [rl.allow("k") for _ in range(3)] == [True, True, False]
    now[0] += 1.0
    assert rl.allow("k") and not rl.allow("k")
    assert rl.allow("other")


def test_dockerfile_entry_point_exists():
    import inspect
    import re
    from pathlib import Path

    from uvicorn.importer import import_from_string

    cmd = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    factory = import_from_string(re.search(r'"(app\.main:\w+)", "--factory"', cmd)[1])
    assert callable(factory) and not inspect.signature(factory).parameters
