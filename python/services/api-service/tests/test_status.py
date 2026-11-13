"""GET /v1/status: every service read in parallel, failures reported (never raised), public-safe,
cached, rate-limited; the runner read with api-service's own token."""

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TARGETS = [
    {"name": "auth-service", "url": "https://auth.lan/health", "kind": "health"},
    {"name": "file-management", "url": "https://files.lan/health", "kind": "health"},
    {"name": "deployer", "url": "https://deploy.lan/health", "kind": "health"},
    {"name": "openbao", "url": "https://bao.lan:8200/v1/sys/health?sealedcode=200&uninitcode=200", "kind": "openbao"},
    {"name": "sandbox-runner", "url": "https://sandbox-runner.lan/v1/status", "kind": "status", "audience": "sandbox-runner"},
]


class Lab:
    """Plays every service behind the status page."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.files_broken = False
        self.deployer_down = False
        self.sealed = False

    def transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            host = request.url.host
            if host == "auth.lan":
                return httpx.Response(200, json={"status": "healthy", "version": "0.1.0", "clients_in_policy": 5})
            if host == "files.lan":
                return httpx.Response(503 if self.files_broken else 200,
                                      json={"status": "degraded" if self.files_broken else "healthy", "storage": "ok"})
            if host == "deploy.lan":
                if self.deployer_down:
                    raise httpx.ConnectError("refused")
                return httpx.Response(200, json={"status": "healthy", "problems": []})
            if host == "bao.lan":
                return httpx.Response(200, json={"initialized": True, "sealed": self.sealed, "version": "2.7.2",
                                                 "cluster_id": "60d5d8f8-secret-ish", "cluster_name": "vault-cluster"})
            if host == "sandbox-runner.lan":
                if request.headers.get("authorization") != "Bearer status-token":
                    return httpx.Response(401, json={"detail": "invalid token"})
                return httpx.Response(200, json={"ok": True, "max": 6, "pods": [{"template": "chat", "state": "warm"}]})
            return httpx.Response(404)
        return httpx.MockTransport(handler)


class StatusToken(httpx.Auth):
    def __init__(self, audience):
        self.audience = audience

    def auth_flow(self, request):
        request.headers["Authorization"] = "Bearer status-token"
        yield request


def client(lab, **settings):
    s = Settings(STATUS_TARGETS=TARGETS, STATUS_CACHE_SECONDS=settings.pop("cache", 0), REVISION="f1ea2f1abc", **settings)
    return TestClient(create_app(s, http=httpx.AsyncClient(transport=lab.transport()), status_auth=StatusToken))


def test_everything_up():
    lab = Lab()
    with client(lab) as c:
        r = c.get("/v1/status")
    page = r.json()
    assert r.status_code == 200 and page["ok"] is True and "checked_at" in page
    assert set(page["services"]) == {"api-service", "auth-service", "file-management", "deployer", "openbao", "sandbox-runner"}
    assert page["services"]["api-service"]["details"]["version"] == "f1ea2f1"
    assert page["services"]["auth-service"]["details"]["clients_in_policy"] == 5
    assert page["services"]["sandbox-runner"]["details"]["pods"] == [{"template": "chat", "state": "warm"}]
    runner = next(c for c in lab.calls if c.url.host == "sandbox-runner.lan")
    assert runner.headers["authorization"] == "Bearer status-token"          # api-service's own token
    assert all("authorization" not in c.headers for c in lab.calls if c.url.host != "sandbox-runner.lan")


def test_failures_are_reported_not_raised():
    lab = Lab()
    lab.files_broken, lab.deployer_down, lab.sealed = True, True, True
    with client(lab) as c:
        r = c.get("/v1/status")
    page = r.json()
    assert r.status_code == 200 and page["ok"] is False
    s = page["services"]
    assert s["file-management"] == {"ok": False, "latency_ms": s["file-management"]["latency_ms"], "error": "HTTP 503",
                                    "details": {"storage": "ok"}}
    assert s["deployer"] == {"ok": False, "error": "unreachable"}
    assert s["openbao"]["ok"] is False and s["openbao"]["details"] == {"initialized": True, "sealed": True, "version": "2.7.2"}
    assert s["auth-service"]["ok"] is True                                     # one failure never hides the rest


def test_public_safe_only():
    lab = Lab()
    with client(lab) as c:
        text = c.get("/v1/status").text
    assert "cluster_id" not in text and "60d5d8f8" not in text and "status-token" not in text and "auth.lan" not in text


def test_cached_so_services_are_read_once_per_period():
    lab = Lab()
    with client(lab, cache=60) as c:
        c.get("/v1/status"), c.get("/v1/status"), c.get("/v1/status")
    assert len(lab.calls) == len(TARGETS)


def test_rate_limited_per_caller():
    lab = Lab()
    with client(lab, cache=60, STATUS_RATE_BURST=2, STATUS_RATE_PER_MINUTE=1) as c:
        codes = [c.get("/v1/status").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_bad_targets_are_refused_at_start():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(STATUS_TARGETS=[{"name": "x", "url": "https://x/health", "kind": "anything"}])
