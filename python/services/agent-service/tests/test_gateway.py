"""The gateway (8443): pairing (C7), the runtime's routes, and the model route (C11, C13)."""

import json

import httpx
import pytest

from app.broker import NotSignedIn
from app.config import Settings
from app.eventlog import EventLog
from app.gateway import UsageCounter, create_gateway
from app.sessions import Sessions
from tests.helpers import ME, PROFILES, FakeRunner, Runtime

TOKEN = "the-real-chatgpt-token"


class FakeLogin:
    def __init__(self):
        self.refreshed = 0
        self.signed_in = True
        self.token = TOKEN

    def headers(self):
        if not self.signed_in:
            raise NotSignedIn("no login")
        return {"authorization": f"Bearer {self.token}", "chatgpt-account-id": "acct"}

    async def refresh(self):
        self.refreshed += 1
        self.token = TOKEN + "-refreshed"


class Streamed(httpx.AsyncByteStream):
    """A body that arrives as a stream, like a real network response (and unlike `content=`)."""

    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        yield self.data


def streamed(status: int, data: bytes, **headers) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "text/event-stream", **headers}, stream=Streamed(data))


def sse(*events: dict) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def completed(total: int) -> dict:
    return {"type": "response.completed",
            "response": {"id": "r", "usage": {"input_tokens": total - 5, "output_tokens": 5, "total_tokens": total}}}


class Upstream:
    """OpenAI's side: records each request, answers with what the test queued."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: list[httpx.Response] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.answers:
            return self.answers.pop(0)
        return streamed(200, sse({"type": "response.output_text.delta", "delta": "hi"}, completed(120)),
                        **{"x-request-id": "req-1"})


@pytest.fixture
async def gw(db):
    runner, login, upstream = FakeRunner(), FakeLogin(), Upstream()
    profiles = dict(PROFILES)
    profiles["pinned"] = PROFILES["voice"].model_copy(update={"model": "gpt-5.5-mini"})
    sessions = Sessions(db, EventLog(db), runner, profiles, Settings())
    up = httpx.AsyncClient(base_url="https://upstream.test/backend-api/codex", transport=httpx.MockTransport(upstream))
    app = create_gateway(sessions, login, up)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://gateway.test")

    async def session(profile="steven-ai"):
        row, _ = await sessions.start(ME, profile)
        claim = runner.claims[-1]
        headers = {"authorization": f"Bearer {claim['capability']}", "x-sandbox-id": str(claim["sandbox_id"])}
        await sessions.ingest(row.id, [Runtime(row.id).ready()])
        return row, headers

    class GW:
        pass
    g = GW()
    g.sessions, g.login, g.upstream, g.client, g.session = sessions, login, upstream, client, session
    yield g
    await client.aclose()
    await up.aclose()


# ---- pairing (C7) ---------------------------------------------------------------------------------
async def test_a_capability_works_only_from_its_own_pod(gw):
    row, headers = await gw.session()
    url = "/v1/runtime/turns?wait=0"
    assert (await gw.client.get(url)).status_code == 401
    assert (await gw.client.get(url, headers={**headers, "authorization": "Bearer nope"})).status_code == 401
    stolen = {**headers, "x-sandbox-id": "00000000-0000-0000-0000-000000000000"}
    assert (await gw.client.get(url, headers=stolen)).status_code == 403          # the CP2 theft test
    assert (await gw.client.get(url, headers={"authorization": headers["authorization"]})).status_code == 403
    assert (await gw.client.get(url, headers=headers)).status_code == 200


async def test_an_ended_sessions_capability_is_dead(gw):
    from app.db.models import EndReason

    row, headers = await gw.session()
    await gw.sessions.end(row.id, EndReason.DELETED)
    assert (await gw.client.get("/v1/runtime/turns?wait=0", headers=headers)).status_code == 401


# ---- the runtime's routes -------------------------------------------------------------------------
async def test_long_poll_and_events(gw):
    row, headers = await gw.session()
    turn, after = await gw.sessions.turn(row.id, ME, "hi")
    r = await gw.client.get("/v1/runtime/turns?wait=1", headers=headers)
    assert r.json() == {"messages": [{"type": "turn", "turn_id": str(turn.id), "text": "hi"}]}
    rt = Runtime(row.id)
    rt.seq = 1                                                    # ready was 0
    batch = rt.answer(turn.id) + [{"type": "junk"}]
    r = await gw.client.post("/v1/runtime/events", headers=headers, json={"events": batch})
    assert r.json() == {"ok": True, "dropped": 1}
    types = [e["type"] for e in await gw.sessions.log.read(row.id, after)]
    assert types == ["turn.started", "message.delta", "message.completed", "turn.completed"]


async def test_event_batches_are_size_limited(gw):
    row, headers = await gw.session()
    big = {"events": [{"type": "text.delta", "text": "x" * (1 << 20)}]}
    assert (await gw.client.post("/v1/runtime/events", headers=headers, json=big)).status_code == 413
    assert (await gw.client.post("/v1/runtime/events", headers=headers, content=b"nope")).status_code == 422


# ---- the model route ------------------------------------------------------------------------------
async def test_the_model_gets_the_real_credential_and_nothing_of_the_sandbox(gw):
    row, headers = await gw.session()
    live = gw.sessions.live_for(headers["authorization"][7:])
    r = await gw.client.post("/model/responses", headers={**headers, "x-forwarded-for": "10.0.0.9", "session_id": "s"},
                             json={"model": "gpt-5.5", "input": "hi"})
    assert r.status_code == 200 and b"response.completed" in r.content
    assert r.headers["x-request-id"] == "req-1"
    (sent,) = gw.upstream.requests
    assert sent.url == "https://upstream.test/backend-api/codex/responses"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}" and sent.headers["chatgpt-account-id"] == "acct"
    for leaked in ("x-sandbox-id", "x-forwarded-for"):
        assert leaked not in sent.headers
    assert headers["authorization"].split()[1] not in str(sent.headers)             # the capability stays here
    assert sent.headers["session_id"] == "s"                                         # Codex's own headers pass
    assert live.turn_tokens == 120                                                   # counted from OpenAI's usage


async def test_an_expired_login_is_refreshed_once(gw):
    row, headers = await gw.session()
    gw.upstream.answers.append(httpx.Response(401, json={"error": "expired"}))
    r = await gw.client.post("/model/responses", headers=headers, json={"input": "hi"})
    assert r.status_code == 200 and gw.login.refreshed == 1
    assert gw.upstream.requests[-1].headers["authorization"] == f"Bearer {TOKEN}-refreshed"


async def test_no_login(gw):
    row, headers = await gw.session()
    gw.login.signed_in = False
    r = await gw.client.post("/model/responses", headers=headers, json={"input": "hi"})
    assert r.status_code == 503 and r.json()["error"]["type"] == "no_login"


async def test_the_token_budget(gw):
    row, headers = await gw.session("smoke")                                         # max_turn_tokens: 20000
    turn, after = await gw.sessions.turn(row.id, ME, "hi")
    live = gw.sessions.live_for(headers["authorization"][7:])
    gw.upstream.answers.append(streamed(200, sse(completed(20_500))))
    assert (await gw.client.post("/model/responses", headers=headers, json={})).status_code == 200
    for _ in range(2):
        r = await gw.client.post("/model/responses", headers=headers, json={})
        assert r.status_code == 403 and r.json()["error"]["type"] == "budget_exceeded"
    assert len(gw.upstream.requests) == 1                                            # nothing more reached OpenAI
    errors = [e for e in await gw.sessions.log.read(row.id, after) if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["code"] == "budget" and errors[0]["turn_id"] == str(turn.id)
    rt = Runtime(row.id)
    rt.seq = 1
    await gw.sessions.ingest(row.id, rt.answer(turn.id))
    await gw.sessions.turn(row.id, ME, "next")                                       # a new turn, a new budget
    assert live.turn_tokens == 0
    assert (await gw.client.post("/model/responses", headers=headers, json={})).status_code == 200


@pytest.mark.parametrize("body, extra, status", [
    ({"model": "gpt-5.5-mini"}, {}, 200),
    ({"model": "gpt-5.5"}, {}, 403),
    ({"input": "no model named"}, {}, 403),
    ({"model": "gpt-5.5-mini"}, {"content-encoding": "zstd"}, 403),                 # can't read it: refused
])
async def test_a_pinned_model_is_enforced(gw, body, extra, status):
    row, headers = await gw.session("pinned")
    r = await gw.client.post("/model/responses", headers={**headers, **extra}, content=json.dumps(body))
    assert r.status_code == status


async def test_an_unpinned_profile_takes_codexs_choice(gw):
    row, headers = await gw.session("steven-ai")                                     # model: null
    r = await gw.client.post("/model/responses", headers=headers, json={"model": "anything"})
    assert r.status_code == 200


def test_usage_counter_reads_split_chunks():
    data = sse({"type": "response.output_text.delta", "delta": "response.completed is just text"},
               completed(70), completed(30))
    counter = UsageCounter()
    for i in range(0, len(data), 7):                                                  # arbitrary chunking
        counter.feed(data[i:i + 7])
    assert counter.tokens == 100
