"""The sessions API (443): scopes, ownership (C2), start (C6), streams, history, the kill switch."""

import asyncio
import json
import uuid

import httpx
import pytest
from fastapi import HTTPException, Request
from service_auth import Principal

from app.api import create_api
from app.config import Settings
from app.history.eventlog import EventLog
from app.sessions import Sessions
from tests.helpers import ME, OTHER, PROFILES, FakeRunner, Runtime

ALL = "chat:read chat:write"


class FakeAuth:
    """ServiceAuth's stand-in: the caller is `X-Test-Caller: <subject> <scope> <scope>…`."""

    def require(self, *scopes):
        async def dependency(request: Request) -> Principal:
            raw = request.headers.get("x-test-caller")
            if not raw:
                raise HTTPException(401, "invalid token")
            subject, *granted = raw.split()
            if not set(scopes) <= set(granted):
                raise HTTPException(403, "insufficient scope")
            return Principal(service=subject.split(":")[-1], scopes=frozenset(granted), subject=subject)
        return dependency


def caller(subject=ME, scopes=ALL) -> dict:
    return {"x-test-caller": f"{subject} {scopes}"}


def frames(body: bytes) -> list[dict]:
    """Parse a text/event-stream body: (id, event, data) per event; comments ignored."""
    out = []
    for block in body.decode().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if fields:
            data = json.loads(fields["data"])
            assert fields["id"] == str(data["seq"]) and fields["event"] == data["type"]
            out.append(data)
    return out


@pytest.fixture
async def api(db):
    runner = FakeRunner()
    sessions = Sessions(db, EventLog(db), runner, PROFILES, Settings())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_api(sessions, FakeAuth())),
                               base_url="https://agent.test")

    class API:
        pass
    a = API()
    a.sessions, a.runner, a.client = sessions, runner, client

    async def start(profile="steven-ai", who=ME, conversation=None, ready=True):
        body = {"profile": profile} | ({"conversation_id": conversation} if conversation else {})
        r = await client.post("/v1/sessions", json=body, headers=caller(who))
        assert r.status_code == 201, r.text
        s = r.json()
        rt = Runtime(uuid.UUID(s["session_id"]))
        if ready:
            await sessions.ingest(uuid.UUID(s["session_id"]), [rt.ready()])
        return s, rt
    a.start = start

    async def runtime_answers(session_id, rt, text="Hello"):
        """The sandbox's side of one turn: pick it up, answer it."""
        (msg,) = await sessions.next_messages(uuid.UUID(session_id), 5)
        await sessions.ingest(uuid.UUID(session_id), rt.answer(uuid.UUID(msg["turn_id"]), text=text))
    a.runtime_answers = runtime_answers
    yield a
    await client.aclose()


# ---- start ----------------------------------------------------------------------------------------
async def test_start_then_continue(api):
    s, _ = await api.start()
    assert s["state"] == "starting" and s["conversation_id"]
    r = await api.client.post("/v1/sessions", json={"profile": "steven-ai", "conversation_id": s["conversation_id"]},
                              headers=caller())
    assert r.status_code == 200 and r.json()["session_id"] == s["session_id"]   # the live one, no new pod
    assert len(api.runner.claims) == 1


@pytest.mark.parametrize("body, who, status, code", [
    ({"profile": "nope"}, ME, 400, "unknown_profile"),
    ({"profile": "voice", "conversation_id": "CONV"}, ME, 409, "profile_mismatch"),
    ({"profile": "steven-ai", "conversation_id": "CONV"}, OTHER, 404, "not_found"),
    ({"profile": "steven-ai", "conversation_id": str(uuid.uuid4())}, ME, 404, "not_found"),
])
async def test_start_errors_are_problem_details(api, body, who, status, code):
    s, _ = await api.start()
    if body.get("conversation_id") == "CONV":
        body["conversation_id"] = s["conversation_id"]
    r = await api.client.post("/v1/sessions", json=body, headers=caller(who))
    assert r.status_code == status
    assert r.headers["content-type"] == "application/problem+json"
    assert r.json()["code"] == code and r.json()["status"] == status and r.json()["detail"]


async def test_no_sandbox_says_when_to_retry(api):
    api.runner.no_sandbox = True
    r = await api.client.post("/v1/sessions", json={"profile": "voice"}, headers=caller())
    assert r.status_code == 503 and r.headers["retry-after"] == "7" and r.json()["code"] == "no_sandbox"


async def test_scopes(api):
    assert (await api.client.post("/v1/sessions", json={"profile": "voice"})).status_code == 401
    r = await api.client.post("/v1/sessions", json={"profile": "voice"}, headers=caller(scopes="chat:read"))
    assert r.status_code == 403
    s, _ = await api.start()
    r = await api.client.get(f"/v1/sessions/{s['session_id']}", headers=caller(scopes="chat:write"))
    assert r.status_code == 403


# ---- a turn, streamed -----------------------------------------------------------------------------
async def test_a_turn_streams_until_it_completes(api):
    s, rt = await api.start()
    post = asyncio.create_task(api.client.post(f"/v1/sessions/{s['session_id']}/turns", json={"text": "hi"},
                                               headers=caller()))
    await api.runtime_answers(s["session_id"], rt)
    r = await asyncio.wait_for(post, 5)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = frames(r.content)
    assert [e["type"] for e in events] == ["turn.started", "message.delta", "message.completed", "turn.completed"]
    assert {e["turn_id"] for e in events} == {r.headers["x-turn-id"]}
    assert events[-1]["usage"]["total_tokens"] == 100


async def test_turn_refusals(api):
    s, rt = await api.start("smoke")                                   # max_turns: 2
    url = f"/v1/sessions/{s['session_id']}/turns"
    first = asyncio.create_task(api.client.post(url, json={"text": "1"}, headers=caller()))
    await asyncio.sleep(0.05)
    r = await api.client.post(url, json={"text": "again"}, headers=caller())
    assert r.status_code == 409 and r.json()["code"] == "busy"
    assert (await api.client.post(url, json={"text": "x"}, headers=caller(OTHER))).status_code == 404
    await api.runtime_answers(s["session_id"], rt)
    await first
    second = asyncio.create_task(api.client.post(url, json={"text": "2"}, headers=caller()))
    await api.runtime_answers(s["session_id"], rt)
    await second
    r = await api.client.post(url, json={"text": "3"}, headers=caller())
    assert r.status_code == 429 and r.json()["code"] == "budget"
    assert (await api.client.post(url, json={"text": ""}, headers=caller())).status_code == 422


async def test_ending_mid_turn_ends_the_stream(api):
    s, rt = await api.start()
    post = asyncio.create_task(api.client.post(f"/v1/sessions/{s['session_id']}/turns", json={"text": "hi"},
                                               headers=caller()))
    await asyncio.sleep(0.05)
    assert (await api.client.delete(f"/v1/sessions/{s['session_id']}", headers=caller())).json() == {"ended": True}
    events = frames((await asyncio.wait_for(post, 5)).content)
    assert events[-1]["type"] == "session.status" and events[-1]["reason"] == "deleted"


# ---- following a session (reconnect) --------------------------------------------------------------
async def test_events_resume_after_a_seq_and_close_at_the_end(api):
    s, rt = await api.start()
    post = asyncio.create_task(api.client.post(f"/v1/sessions/{s['session_id']}/turns", json={"text": "hi"},
                                               headers=caller()))
    await api.runtime_answers(s["session_id"], rt)
    await post
    await api.client.delete(f"/v1/sessions/{s['session_id']}", headers=caller())
    url = f"/v1/sessions/{s['session_id']}/events"
    everything = frames((await api.client.get(url, headers=caller())).content)
    assert everything[0]["type"] == "session.status" and everything[-1]["status"] == "ended"
    seqs = [e["seq"] for e in everything]
    by_query = frames((await api.client.get(f"{url}?after={seqs[2]}", headers=caller())).content)
    by_header = frames((await api.client.get(url, headers={**caller(), "last-event-id": str(seqs[2])})).content)
    assert [e["seq"] for e in by_query] == [e["seq"] for e in by_header] == seqs[3:]
    assert (await api.client.get(url, headers=caller(OTHER))).status_code == 404


# ---- state, cancel, end ---------------------------------------------------------------------------
async def test_state_cancel_and_end(api):
    s, rt = await api.start()
    sid = s["session_id"]
    assert (await api.client.get(f"/v1/sessions/{sid}", headers=caller())).json()["state"] == "ready"
    assert (await api.client.post(f"/v1/sessions/{sid}/cancel", headers=caller())).json() == {"interrupted": False}
    assert (await api.client.delete(f"/v1/sessions/{sid}", headers=caller(OTHER))).status_code == 404
    admin = caller("service:someone", "chat:read chat:write chat:admin")
    assert (await api.client.get(f"/v1/sessions/{sid}", headers=admin)).status_code == 200    # admin: any session
    assert (await api.client.delete(f"/v1/sessions/{sid}", headers=caller())).json() == {"ended": True}
    got = (await api.client.get(f"/v1/sessions/{sid}", headers=caller())).json()
    assert got["state"] == "ended" and got["end_reason"] == "deleted" and got["ready_at"]
    r = await api.client.post(f"/v1/sessions/{sid}/turns", json={"text": "late"}, headers=caller())
    assert r.status_code == 409 and r.json()["code"] == "ended"


# ---- history --------------------------------------------------------------------------------------
async def test_conversations_and_their_turns_across_sessions(api):
    s, rt = await api.start("voice")
    url = f"/v1/sessions/{s['session_id']}/turns"
    post = asyncio.create_task(api.client.post(url, json={"text": "first"}, headers=caller()))
    await api.runtime_answers(s["session_id"], rt, "one")
    await post
    await api.client.delete(f"/v1/sessions/{s['session_id']}", headers=caller())
    s2, rt2 = await api.start("voice", conversation=s["conversation_id"])
    post = asyncio.create_task(api.client.post(f"/v1/sessions/{s2['session_id']}/turns", json={"text": "second"},
                                               headers=caller()))
    await api.runtime_answers(s2["session_id"], rt2, "two")
    await post
    await api.start("steven-ai", who=OTHER)                            # someone else's conversation
    mine = (await api.client.get("/v1/conversations", headers=caller())).json()["conversations"]
    assert [c["conversation_id"] for c in mine] == [s["conversation_id"]]
    assert mine[0]["live_session_id"] == s2["session_id"]
    turns = (await api.client.get(f"/v1/conversations/{s['conversation_id']}/turns?events=true",
                                  headers=caller())).json()["turns"]
    assert [(t["user_text"], t["reply_text"]) for t in turns] == [("first", "one"), ("second", "two")]
    assert turns[0]["session_id"] != turns[1]["session_id"]
    assert [e["type"] for e in turns[1]["events"]][-1] == "turn.completed"
    r = await api.client.get(f"/v1/conversations/{s['conversation_id']}/turns", headers=caller(OTHER))
    assert r.status_code == 404


# ---- the kill switch and the status page ----------------------------------------------------------
async def test_kill_switch(api):
    s, _ = await api.start()
    assert (await api.client.post("/v1/admin/stop", headers=caller())).status_code == 403     # needs chat:admin
    admin = caller(ME, "chat:read chat:write chat:admin")
    assert (await api.client.post("/v1/admin/stop", headers=admin)).json() == {"stopped": True, "sessions_ended": 1}
    r = await api.client.post("/v1/sessions", json={"profile": "voice"}, headers=caller())
    assert r.status_code == 503 and r.json()["code"] == "stopped"
    assert (await api.client.post("/v1/admin/start", headers=admin)).json() == {"stopped": False}
    assert (await api.client.post("/v1/sessions", json={"profile": "voice"}, headers=caller())).status_code == 201


async def test_status_page(api):
    await api.start()
    await api.start("voice", ready=False)
    assert (await api.client.get("/v1/status", headers=caller())).status_code == 403
    r = await api.client.get("/v1/status", headers=caller("service:api-service", "status:read"))
    assert r.json() == {"ok": True, "stopped": False, "database": "ok",
                        "sessions": {"starting": 1, "ready": 1, "busy": 0}}
    assert "session_id" not in r.text                                  # counts only, never ids


async def test_health_needs_no_token(api):
    assert (await api.client.get("/health")).json() == {"status": "healthy"}
