"""The `agent` CLI (app/cli.py) against a fake agent-service."""

import io
import json

import httpx
import pytest

from app import cli


def sse(*events: dict) -> bytes:
    out = b": keep-alive\n\n"
    for i, e in enumerate(events):
        out += f"id: {i}\nevent: {e['type']}\ndata: {json.dumps({'seq': i, **e})}\n\n".encode()
    return out


TURN = sse({"type": "turn.started"}, {"type": "message.delta", "text": "Hel"}, {"type": "message.delta", "text": "lo"},
           {"type": "message.completed", "text": "Hello"},
           {"type": "turn.completed", "status": "completed", "usage": {"total_tokens": 5732}})


class FakeAgentService:
    def __init__(self, conversations=()):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.conversations = list(conversations)

    def __call__(self, r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content) if r.content else None
        self.requests.append((r.method, r.url.path, body))
        if r.url.path == "/v1/conversations":
            return httpx.Response(200, json={"conversations": self.conversations})
        if r.url.path == "/v1/sessions":
            if body["profile"] == "nope":
                return httpx.Response(400, json={"status": 400, "code": "unknown_profile", "detail": "unknown profile: nope"})
            status = 200 if body.get("conversation_id") == "c-live" else 201
            return httpx.Response(status, json={"session_id": "s-12345678", "conversation_id": body.get("conversation_id", "c-new")})
        if r.url.path.endswith("/turns"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=TURN)
        return httpx.Response(200, json={"ended": True})


def agent(fake) -> cli.Agent:
    return cli.Agent(httpx.AsyncClient(base_url="https://agent.test", transport=httpx.MockTransport(fake)))


async def test_read_events_parses_sse_and_skips_keep_alives():
    resp = httpx.Response(200, content=TURN)
    events = [e async for e in cli.read_events(resp)]
    assert [e["type"] for e in events][0] == "turn.started" and len(events) == 5


def test_printer_streams_the_reply_and_summarises(capsys):
    out = io.StringIO()
    p = cli.Printer(out)
    for e in [{"type": "message.delta", "text": "Hel"}, {"type": "message.delta", "text": "lo"},
              {"type": "tool.started", "name": "shell", "input": {"cmd": "ls"}},
              {"type": "tool.completed", "is_error": False, "output": "a.txt"},
              {"type": "error", "code": "budget", "message": "this turn used its 20000 tokens"},
              {"type": "turn.completed", "status": "failed", "reason": "budget", "usage": {"total_tokens": 20500}}]:
        p.event(e)
    assert out.getvalue() == "Hello\n"                         # only the reply on stdout: pipeable
    err = capsys.readouterr().err
    assert "⚙ shell" in err and "✓ a.txt" in err and "error (budget)" in err
    assert "(failed · budget ·" in err and "20,500 tokens" in err


async def test_ask_starts_a_session_and_streams(capsys):
    fake = FakeAgentService()
    args = cli.parser().parse_args(["ask", "-p", "voice", "say", "hello"])
    assert await cli.ask(agent(fake), args) == 0
    assert fake.requests[0] == ("POST", "/v1/sessions", {"profile": "voice"})
    assert fake.requests[1] == ("POST", "/v1/sessions/s-12345678/turns", {"text": "say hello"})
    out, err = capsys.readouterr()
    assert out == "Hello\n" and "new session s-123456" in err and "5,732 tokens" in err
    assert not any(m == "DELETE" for m, _, _ in fake.requests)    # ask leaves the session to the idle timer


async def test_last_continues_the_latest_conversation_of_that_profile(capsys):
    fake = FakeAgentService([{"conversation_id": "c-live", "profile": "pi"},
                             {"conversation_id": "c-voice", "profile": "voice"}])
    args = cli.parser().parse_args(["ask", "--last", "-p", "voice", "again"])
    await cli.ask(agent(fake), args)
    assert fake.requests[1] == ("POST", "/v1/sessions", {"profile": "voice", "conversation_id": "c-voice"})
    args = cli.parser().parse_args(["ask", "--last", "again"])            # pi (the default): its live session
    await cli.ask(agent(fake), args)
    assert "continuing session" in capsys.readouterr().err


async def test_errors_are_problem_details():
    with pytest.raises(cli.CLIError, match="unknown profile: nope"):
        await agent(FakeAgentService()).session("nope", None, False)
    with pytest.raises(cli.CLIError, match="no earlier conversation"):
        await agent(FakeAgentService()).session("voice", None, True)


def test_the_commands():
    p = cli.parser()
    assert p.parse_args(["stop"]).run is cli.kill_switch
    assert p.parse_args(["history", "c1", "--events"]).events is True
    with pytest.raises(SystemExit):
        p.parse_args(["ask", "-c", "x", "--last", "hi"])            # one or the other
