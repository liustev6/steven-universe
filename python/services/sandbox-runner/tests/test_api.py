"""The API (sandbox-runner.md "API"): auth, claim, release, the pool, health.

Tokens are checked by service-auth's ServiceAuth (its own tests cover the JWT side); here a stand-in
with the same `require(*scopes)` shape checks that each route asks for its scope.
"""

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.api import create_app
from tests.conftest import CAP, PROFILE

TOKENS = {  # bearer -> scopes, as auth-service would grant them
    "agent": {"sandboxes:claim", "sandboxes:release", "sandboxes:read"},
    "reader": {"sandboxes:read"},
}


class FakeAuth:
    def __init__(self):
        self.asked = set()

    def require(self, *scopes):
        self.asked.update(scopes)

        async def dependency(request: Request):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in TOKENS:
                raise HTTPException(401, "invalid token")
            if not set(scopes) <= TOKENS[token]:
                raise HTTPException(403, "insufficient scope")
        return dependency


def client(make_pool, settings, **overrides):
    pool = make_pool(**overrides)
    auth = FakeAuth()
    return TestClient(create_app(settings, pool, auth)), pool


def wait_warm(c, n):
    import time
    for _ in range(300):
        if c.get("/health").json()["warm"]["chat"] >= n:
            return
        time.sleep(0.01)
    raise AssertionError("not warm")


H = {"Authorization": "Bearer agent"}


def test_claim_release_and_the_pool(make_pool, settings):
    c, pool = client(make_pool, settings)
    with c:
        wait_warm(c, 2)
        body = {"template": "chat", "session_id": "s", "capability": CAP, "profile": PROFILE}
        assert c.post("/v1/sandboxes/claim", json=body).status_code == 401
        r = c.post("/v1/sandboxes/claim", headers=H, json=body)
        assert r.status_code == 200
        sid, gw = r.json()["sandbox_id"], r.json()["gateway_url"]
        assert gw.startswith("http://172.29.") and gw.endswith(":8080")
        listing = c.get("/v1/sandboxes", headers=H).json()
        assert listing["max"] == 6 and listing["templates"]["chat"]["claimed"] == 1
        assert listing["templates"]["dev"] == {"image": "ghcr.io/x/agent-runtime-dev@sha256:dev", "runtime": "runsc",
                                               "target_warm": 0, "max": 3, "warm": 0, "starting": 0, "claimed": 0}
        assert {p["sandbox_id"]: p["state"] for p in listing["pods"]}[sid] == "claimed"
        assert c.post(f"/v1/sandboxes/{sid}/release", headers=H).json() == {"ok": True}
        assert c.post(f"/v1/sandboxes/{sid}/release", headers=H).status_code == 404
        assert c.post("/v1/sandboxes/not-a-uuid/release", headers=H).status_code == 422


def test_full_pool_is_503_with_retry_after(make_pool, settings):
    c, pool = client(make_pool, settings, MAX=1)
    with c:
        wait_warm(c, 1)
        body = {"template": "chat", "session_id": "s", "capability": CAP, "profile": PROFILE}
        assert c.post("/v1/sandboxes/claim", headers=H, json=body).status_code == 200
        r = c.post("/v1/sandboxes/claim", headers=H, json=body)
        assert r.status_code == 503 and r.headers["retry-after"] == "5"


def test_claim_body_is_strict(make_pool, settings):
    c, _ = client(make_pool, settings)
    with c:
        wait_warm(c, 2)
        good = {"template": "chat", "session_id": "s", "capability": CAP, "profile": {}}
        assert c.post("/v1/sandboxes/claim", headers=H, json={**good, "capability": "short"}).status_code == 422
        assert c.post("/v1/sandboxes/claim", headers=H, json={**good, "image": "evil"}).status_code == 422
        assert c.post("/v1/sandboxes/claim", headers=H, json={**good, "template": "nope"}).status_code == 400


def test_each_route_needs_its_scope(make_pool, settings):
    auth = FakeAuth()
    c = TestClient(create_app(settings, make_pool(), auth))
    assert auth.asked == {"sandboxes:claim", "sandboxes:release", "sandboxes:read"}
    with c:
        wait_warm(c, 2)
        reader = {"Authorization": "Bearer reader"}
        body = {"template": "chat", "session_id": "s", "capability": CAP, "profile": PROFILE}
        assert c.get("/v1/sandboxes", headers=reader).status_code == 200
        assert c.post("/v1/sandboxes/claim", headers=reader, json=body).status_code == 403
        assert c.post("/v1/sandboxes/claim", headers={"Authorization": "Bearer forged"}, json=body).status_code == 401
        assert c.get("/health").status_code == 200                       # no auth: monitoring
