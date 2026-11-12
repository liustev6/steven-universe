"""The API (sandbox-runner.md "API"): auth, claim, release, the pool, health."""

from fastapi.testclient import TestClient

from app.api import create_app
from tests.conftest import CAP, PROFILE


def client(make_pool, settings, **overrides):
    pool = make_pool(**overrides)
    return TestClient(create_app(settings, pool)), pool


def wait_warm(c, n):
    import time
    for _ in range(300):
        if c.get("/health").json()["warm"] >= n:
            return
        time.sleep(0.01)
    raise AssertionError("not warm")


H = {"Authorization": "Bearer dev-token"}


def test_claim_release_and_the_pool(make_pool, settings):
    c, pool = client(make_pool, settings)
    with c:
        wait_warm(c, 2)
        assert c.post("/v1/sandboxes/claim", json={"session_id": "s", "capability": CAP, "profile": PROFILE}).status_code == 401
        r = c.post("/v1/sandboxes/claim", headers=H, json={"session_id": "s", "capability": CAP, "profile": PROFILE})
        assert r.status_code == 200
        sid, gw = r.json()["sandbox_id"], r.json()["gateway_url"]
        assert gw.startswith("http://172.29.") and gw.endswith(":8080")
        listing = c.get("/v1/sandboxes", headers=H).json()
        assert listing["claimed"] == 1 and listing["max"] == 3
        assert {p["sandbox_id"]: p["state"] for p in listing["pods"]}[sid] == "claimed"
        assert c.post(f"/v1/sandboxes/{sid}/release", headers=H).json() == {"ok": True}
        assert c.post(f"/v1/sandboxes/{sid}/release", headers=H).status_code == 404
        assert c.post("/v1/sandboxes/not-a-uuid/release", headers=H).status_code == 422


def test_full_pool_is_503_with_retry_after(make_pool, settings):
    c, pool = client(make_pool, settings, WARM=1, MAX=1)
    with c:
        wait_warm(c, 1)
        body = {"session_id": "s", "capability": CAP, "profile": PROFILE}
        assert c.post("/v1/sandboxes/claim", headers=H, json=body).status_code == 200
        r = c.post("/v1/sandboxes/claim", headers=H, json=body)
        assert r.status_code == 503 and r.headers["retry-after"] == "5"


def test_claim_body_is_strict(make_pool, settings):
    c, _ = client(make_pool, settings)
    with c:
        wait_warm(c, 2)
        assert c.post("/v1/sandboxes/claim", headers=H, json={"session_id": "s", "capability": "short", "profile": {}}).status_code == 422
        assert c.post("/v1/sandboxes/claim", headers=H, json={"session_id": "s", "capability": CAP, "profile": {}, "image": "evil"}).status_code == 422
