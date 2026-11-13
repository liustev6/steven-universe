"""Edge rules every route goes through: deny-by-default modes, headers, client IP, rate limiter."""

import pytest
from starlette.requests import Request

from app.config import Settings
from app.edge import Mode, client_ip
from app.ratelimit import RateLimiter

# The reviewed list of routes per mode (spec E2): a new public or proxy route must be added here.
EXPECTED = {
    ("GET", "/health"): Mode.PUBLIC,
    ("GET", "/v1/status"): Mode.PUBLIC,          # the lab's status page: public-safe by design
    ("POST", "/deploy/v1/deployments"): Mode.PROXY,
    ("GET", "/deploy/v1/deployments/{deployment_id}/events"): Mode.PROXY,
}


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
