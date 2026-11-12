"""The dev gateway: capability and dev-token checks, the session file, the credential swap on /model."""

import asyncio
import json
import stat

import httpx
import pytest

from agent_dev.broker import AuthBroker
from agent_dev.gateway import Settings, create_app


def write_login(home, token):
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "last_refresh": token, "tokens": {
        "access_token": token, "refresh_token": "rt", "id_token": "it", "account_id": "acct-1"}}))


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "key").write_text("sk-real-key\n")
    (tmp_path / "token").write_text("dev-token\n")
    (tmp_path / "p.yaml").write_text("name: general\ninstructions: hi\n")
    write_login(tmp_path / "codex-home", "at-real-1")
    return Settings(profile_file=tmp_path / "p.yaml", session_dir=tmp_path / "session", codex_home=tmp_path / "codex-home",
                    api_key_file=tmp_path / "key", dev_token_file=tmp_path / "token", gateway_url="http://gw:8080")


def run(app, coro_fn):
    async def go():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw:8080") as c:
                return await coro_fn(c)
    return asyncio.run(go())


def test_session_file(settings):
    app = create_app(settings)

    async def go(c):
        return json.loads((settings.session_dir / "session.json").read_text()), (settings.session_dir / "session.json").stat()
    s, st = run(app, go)
    assert s["capability"] == app.state.dev.capability and s["gateway_url"] == "http://gw:8080"
    assert s["profile"]["name"] == "general" and stat.S_IMODE(st.st_mode) == 0o444
    assert "at-real-1" not in json.dumps(s) and "rt" not in s.values()   # the login never reaches the sandbox


def test_auth(settings):
    app = create_app(settings)

    async def go(c):
        cap = app.state.dev.capability
        return [
            (await c.get("/v1/runtime/turns", params={"wait": 0})).status_code,
            (await c.get("/v1/runtime/turns", params={"wait": 0}, headers={"Authorization": "Bearer dev-token"})).status_code,
            (await c.get("/v1/runtime/turns", params={"wait": 0}, headers={"Authorization": f"Bearer {cap}"})).status_code,
            (await c.post("/dev/end", headers={"Authorization": f"Bearer {cap}"})).status_code,   # capability isn't a dev token
            (await c.post("/dev/end", headers={"Authorization": "Bearer dev-token"})).status_code,
            (await c.get("/dev/health")).status_code,
        ]
    assert run(app, go) == [401, 401, 200, 401, 200, 200]


def fake_upstream(seen, statuses=None):
    statuses = list(statuses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "headers": dict(request.headers), "body": request.content})
        status = statuses.pop(0) if statuses else 200
        return httpx.Response(status, headers={"content-type": "text/event-stream", "x-codex-primary-used-percent": "40",
                                               "set-cookie": "no"},
                              stream=httpx.ByteStream(b"event: response.completed\n\n"))
    return httpx.MockTransport(handler)


def ask_model(app, capability=None, path="/model/responses"):
    async def go(c):
        cap = capability or app.state.dev.capability
        return await c.post(path, content=b'{"model":"m","stream":true}',
                            headers={"Authorization": f"Bearer {cap}", "content-type": "application/json",
                                     "chatgpt-account-id": "spoofed", "originator": "codex_python_sdk"})
    return run(app, go)


def test_model_route_adds_your_subscription_token(settings):
    seen = []
    app = create_app(settings, upstream_transport=fake_upstream(seen))
    r = ask_model(app)
    assert r.status_code == 200 and r.text == "event: response.completed\n\n"
    assert seen[0]["url"] == "https://chatgpt.com/backend-api/codex/responses"
    h = seen[0]["headers"]
    assert h["authorization"] == "Bearer at-real-1" and h["chatgpt-account-id"] == "acct-1"   # not the sandbox's value
    assert h["originator"] == "codex_python_sdk" and h["accept-encoding"] == "identity"
    assert seen[0]["body"] == b'{"model":"m","stream":true}'
    assert r.headers["x-codex-primary-used-percent"] == "40" and "set-cookie" not in r.headers
    assert ask_model(app, capability="guess").status_code == 401


def test_an_expired_token_is_refreshed_once_then_retried(settings):
    seen, refreshed = [], []

    async def refresher(home):
        refreshed.append(home)
        write_login(home, "at-real-2")
    broker = AuthBroker(settings.codex_home, refresher=refresher)
    app = create_app(settings, upstream_transport=fake_upstream(seen, statuses=[401]), broker=broker)
    assert ask_model(app).status_code == 200
    assert [s["headers"]["authorization"] for s in seen] == ["Bearer at-real-1", "Bearer at-real-2"]
    assert refreshed == [settings.codex_home]


def test_no_login_is_a_clear_503(settings):
    (settings.codex_home / "auth.json").unlink()
    seen = []
    app = create_app(settings, upstream_transport=fake_upstream(seen))
    assert ask_model(app).status_code == 503 and seen == []


def test_api_key_mode(settings):
    seen = []
    app = create_app(settings.model_copy(update={"model_auth": "apikey"}), upstream_transport=fake_upstream(seen))
    assert ask_model(app).status_code == 200
    assert seen[0]["url"] == "https://api.openai.com/v1/responses"
    assert seen[0]["headers"]["authorization"] == "Bearer sk-real-key" and "chatgpt-account-id" not in seen[0]["headers"]


def test_events_are_parsed_one_by_one(settings):
    app = create_app(settings)
    good = {"v": 1, "session_id": "s", "turn_id": "t1", "seq": 0, "ts": 1.0, "type": "text.delta", "text": "hi"}

    async def go(c):
        cap = app.state.dev.capability
        r = await c.post("/v1/runtime/events", headers={"Authorization": f"Bearer {cap}"}, json={"events": [
            good, {**good, "seq": 1, "type": "made.up"}, {**good, "seq": 2, "text": None}, {**good, "seq": 3}]})
        return r.json(), [e["seq"] for e in app.state.dev.events]
    body, kept = run(app, go)
    assert body == {"ok": True, "dropped": 2} and kept == [0, 3]       # one bad event never loses the batch


# ---- runner mode (CP2): the pod comes from sandbox-runner; the capability only works from it ------

SANDBOX = "3f2b8c1e-7a4d-4e9b-9c2a-1d5e6f708192"


def fake_runner(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/v1/sandboxes/claim":
            return httpx.Response(200, json={"sandbox_id": SANDBOX, "gateway_url": "http://172.29.0.2:8080"})
        return httpx.Response(200, json={"ok": True})
    return httpx.AsyncClient(base_url="http://runner:8443", transport=httpx.MockTransport(handler))


def test_runner_mode_claims_at_start_and_releases_at_the_end(settings):
    calls = []
    app = create_app(settings.model_copy(update={"runner_url": "http://runner:8443"}), runner=fake_runner(calls))
    run(app, lambda c: asyncio.sleep(0))
    (m1, p1, claim), (m2, p2, _) = calls
    assert (m1, p1) == ("POST", "/v1/sandboxes/claim") and (m2, p2) == ("POST", f"/v1/sandboxes/{SANDBOX}/release")
    assert claim["template"] == "chat" and claim["capability"] == app.state.dev.capability
    assert claim["session_id"] == app.state.dev.session_id and claim["profile"]["name"] == "general"
    assert not (settings.session_dir / "session.json").exists()          # the runner writes it, in the pod


def test_runner_mode_capability_only_works_from_its_own_pod(settings):
    seen = []
    app = create_app(settings.model_copy(update={"runner_url": "http://runner:8443"}),
                     upstream_transport=fake_upstream(seen), runner=fake_runner([]))

    async def go(c):
        cap = {"Authorization": f"Bearer {app.state.dev.capability}"}
        poll = lambda extra: c.get("/v1/runtime/turns", params={"wait": 0}, headers={**cap, **extra})
        return [
            (await poll({})).status_code,                                         # no stamp
            (await poll({"X-Sandbox-Id": "another-pod"})).status_code,           # someone else's pod
            (await poll({"X-Sandbox-Id": SANDBOX})).status_code,                 # its own pod
            (await c.post("/model/responses", content=b"{}", headers={**cap, "X-Sandbox-Id": "another-pod"})).status_code,
            (await c.post("/model/responses", content=b"{}", headers={**cap, "X-Sandbox-Id": SANDBOX})).status_code,
        ]
    assert run(app, go) == [403, 403, 200, 403, 200]
    assert len(seen) == 1 and "x-sandbox-id" not in seen[0]["headers"]        # the stamp never reaches OpenAI
