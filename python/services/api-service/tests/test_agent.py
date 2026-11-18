"""/v1/agent/…: mode `client`, the token exchange (grant 3, E4), and agent-service's answers passed back."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import FakeAuth, FakeTokens

SESSION = "6f1c2a3b-0000-4000-8000-000000000001"
CONVERSATION = "6f1c2a3b-0000-4000-8000-000000000002"


def streamed(status: int, body, headers: dict | None = None) -> httpx.Response:
    """An answer that arrives as a stream, like a real network one (`json=` would count as already read)."""
    import json as _json

    data = body if isinstance(body, bytes) else _json.dumps(body).encode()

    async def stream():
        yield data
    return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})}, content=stream())


class FakeAgentService:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.down = False

    def transport(self):
        async def handler(r: httpx.Request) -> httpx.Response:
            self.requests.append(r)
            if self.down:
                raise httpx.ConnectError("refused")
            if r.url.path.endswith("/turns") and r.method == "POST":
                async def stream():
                    yield b'id: 0\nevent: turn.started\ndata: {"seq":0,"type":"turn.started"}\n\n'
                    yield b'id: 1\nevent: turn.completed\ndata: {"seq":1,"type":"turn.completed"}\n\n'
                return httpx.Response(200, headers={"content-type": "text/event-stream", "x-turn-id": "t-1"},
                                      content=stream())
            if r.url.path == "/v1/sessions" and b"nope" in r.content:
                return streamed(400, {"status": 400, "code": "unknown_profile", "detail": "unknown profile: nope"},
                                {"content-type": "application/problem+json"})
            if r.url.path == "/v1/sessions":
                if b"busy" in r.content:
                    return streamed(503, {"status": 503, "code": "no_sandbox"},
                                    {"retry-after": "5", "content-type": "application/problem+json"})
                return streamed(201, {"session_id": SESSION})
            return streamed(200, {"ok": True})
        return httpx.MockTransport(handler)


@pytest.fixture
def agent_service():
    return FakeAgentService()


@pytest.fixture
def tokens():
    return FakeTokens()


@pytest.fixture
def api(agent_service, tokens):
    settings = Settings(AGENT_RATE_BURST=5, AGENT_RATE_PER_MINUTE=1, AGENT_MAX_BODY_BYTES=1000)
    app = create_app(settings, http=httpx.AsyncClient(transport=agent_service.transport()),
                     agent_auth=FakeAuth(), agent_tokens=tokens)
    with TestClient(app) as c:
        yield c


def caller(subject="service:smoke-test", scopes="chat:read chat:write"):
    return {"x-test-caller": f"{subject} {scopes}"}


ROUTES = [
    ("POST", "/v1/agent/sessions", "chat:write", "/v1/sessions"),
    ("POST", f"/v1/agent/sessions/{SESSION}/turns", "chat:write", f"/v1/sessions/{SESSION}/turns"),
    ("GET", f"/v1/agent/sessions/{SESSION}/events", "chat:read", f"/v1/sessions/{SESSION}/events"),
    ("GET", f"/v1/agent/sessions/{SESSION}", "chat:read", f"/v1/sessions/{SESSION}"),
    ("POST", f"/v1/agent/sessions/{SESSION}/cancel", "chat:write", f"/v1/sessions/{SESSION}/cancel"),
    ("DELETE", f"/v1/agent/sessions/{SESSION}", "chat:write", f"/v1/sessions/{SESSION}"),
    ("GET", "/v1/agent/conversations", "chat:read", "/v1/conversations"),
    ("GET", f"/v1/agent/conversations/{CONVERSATION}/turns", "chat:read", f"/v1/conversations/{CONVERSATION}/turns"),
]


@pytest.mark.parametrize("method, path, scope, upstream", ROUTES)
def test_each_route_needs_a_token_and_its_scope_then_relays(api, agent_service, tokens, method, path, scope, upstream):
    body = {"text": "hi"} if path.endswith("/turns") and method == "POST" else ({"profile": "smoke"} if method == "POST" and path.endswith("sessions") else None)
    assert api.request(method, path, json=body).status_code == 401
    other = "chat:read" if scope == "chat:write" else "chat:write"
    assert api.request(method, path, json=body, headers=caller(scopes=other)).status_code == 403
    r = api.request(method, path, json=body, headers=caller(scopes=scope))
    assert r.status_code in (200, 201)
    sent = agent_service.requests[-1]
    assert sent.url.path == upstream and sent.method == method
    # E4: the caller's own token never leaves api-service; agent-service gets the exchanged one.
    assert sent.headers["authorization"] == "Bearer exchanged:token-of-service:smoke-test"
    assert tokens.exchanged[-1] == ("token-of-service:smoke-test", "agent-service")


def test_a_turn_streams_back_with_its_id(api):
    r = api.post(f"/v1/agent/sessions/{SESSION}/turns", json={"text": "hi"}, headers=caller())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-turn-id"] == "t-1" and r.headers["x-accel-buffering"] == "no"
    assert r.content.count(b"event: ") == 2


def test_agent_services_problem_details_pass_through(api):
    r = api.post("/v1/agent/sessions", json={"profile": "nope"}, headers=caller())
    assert r.status_code == 400 and r.headers["content-type"] == "application/problem+json"
    assert r.json()["code"] == "unknown_profile"
    r = api.post("/v1/agent/sessions", json={"profile": "busy"}, headers=caller())
    assert r.status_code == 503 and r.headers["retry-after"] == "5"


def test_reconnect_passes_last_event_id_and_after(api, agent_service):
    api.get(f"/v1/agent/sessions/{SESSION}/events?after=7", headers={**caller(), "last-event-id": "9"})
    sent = agent_service.requests[-1]
    assert sent.url.params["after"] == "7" and sent.headers["last-event-id"] == "9"


def test_only_machine_clients_calling_as_themselves(api):
    r = api.get("/v1/agent/conversations", headers=caller(scopes="chat:read +act"))
    assert r.status_code == 403
    r = api.get("/v1/agent/conversations", headers=caller("user:42", "chat:read"))
    assert r.status_code == 403


def test_a_client_without_agent_access_is_refused_at_the_exchange(api):
    r = api.get("/v1/agent/conversations", headers=caller("service:pi-server", "chat:read"))
    assert r.status_code == 403 and r.json()["error"] == "forbidden"


def test_limits(api, agent_service):
    big = {"text": "x" * 2000}
    assert api.post(f"/v1/agent/sessions/{SESSION}/turns", json=big, headers=caller()).status_code == 413
    assert api.get("/v1/agent/sessions/not-a-uuid", headers=caller()).status_code == 422   # ids are UUIDs
    agent_service.down = True
    assert api.get("/v1/agent/conversations", headers=caller()).status_code == 502
    agent_service.down = False
    codes = [api.get("/v1/agent/conversations", headers=caller("service:other", "chat:read")).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429                                      # per client


def test_agent_services_admin_and_status_are_not_relayed(api):
    for path in ("/v1/agent/admin/stop", "/v1/agent/status"):
        assert api.post(path, headers=caller()).status_code in (404, 405)
