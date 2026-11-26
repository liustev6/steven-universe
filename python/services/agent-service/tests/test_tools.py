"""Tools (docs/specs/agent-service/tools.md): a session's tools fixed at its start (TL7), the gateway's
/mcp paired like every gateway route (C7), each call with the turn's caller's token (TL4), within
the session's budget (TL10). tools-service itself is faked; the MCP sides are the real SDK."""

import uuid

import httpx
import httpx2
import pytest
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from service_auth import TokenRequestError

from app.config import Settings
from app.db.models import ChatSession
from app.gateway import create_gateway
from app.history.eventlog import EventLog
from app.sessions import Sessions
from app.tools_service import NoTools, ToolsService
from tests.helpers import ME, PROFILES, FakeRunner, Runtime

WEATHER = [{"name": "weather_current", "description": "now", "inputSchema": {"type": "object", "properties": {}}},
           {"name": "weather_forecast", "description": "days", "inputSchema": {"type": "object", "properties": {}}}]


class FakeTools:
    """tools-service as the sessions core sees it: what it was asked, with which caller token."""

    def __init__(self):
        self.lists: list[tuple] = []
        self.calls: list[tuple] = []
        self.fail: Exception | None = None

    async def list(self, caller_token, mcp_tools):
        self.lists.append((caller_token, mcp_tools))
        if self.fail:
            raise self.fail
        return WEATHER

    async def call(self, caller_token, mcp_tools, name, arguments):
        self.calls.append((caller_token, mcp_tools, name, arguments))
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"{name} ok")],
                                    structured_content={"tool": name, "arguments": arguments}), "jti-1"


class FakeLogin:
    def headers(self):
        return {"authorization": "Bearer x"}

    async def refresh(self):
        pass


@pytest.fixture
async def env(db):
    runner, tools = FakeRunner(), FakeTools()
    sessions = Sessions(db, EventLog(db), runner, dict(PROFILES), Settings(), tools)
    up = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    gateway = create_gateway(sessions, FakeLogin(), up)

    class Env:
        pass
    e = Env()
    e.sessions, e.runner, e.tools, e.gateway, e.db = sessions, runner, tools, gateway, db

    async def start(profile="chat", caller_token="caller-at-start"):
        row, _ = await sessions.start(ME, profile, caller_token=caller_token)
        claim = runner.claims[-1]
        await sessions.ingest(row.id, [Runtime(row.id).ready()])
        return row, {"authorization": f"Bearer {claim['capability']}", "x-sandbox-id": str(claim["sandbox_id"])}

    def mcp(headers):
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=gateway), headers=headers,
                                  base_url="https://gateway.test")
        return Client(streamable_http_client("https://gateway.test/mcp", http_client=http))

    e.start, e.mcp = start, mcp
    # /mcp's task group, entered in each test that uses it (an async fixture's teardown runs in
    # another task, where anyio won't close it)
    e.serving = gateway.state.mcp.session_manager.run
    yield e
    await up.aclose()


# ---- a session's tools, fixed when it starts (TL5, TL7) --------------------------------------------
async def test_a_session_gets_the_owners_tools_when_it_starts(env):
    row, _ = await env.start("chat")
    assert env.tools.lists == [("caller-at-start", None)]           # no mcp_tools: the owner's whole grant
    async with env.db() as db:
        assert (await db.get(ChatSession, row.id)).tools == WEATHER   # stored: it survives a restart


async def test_a_profile_narrows_or_turns_them_off(env):
    await env.start("tooled")
    assert env.tools.lists[-1] == ("caller-at-start", ["weather/"])
    row, _ = await env.start("toolless")
    assert len(env.tools.lists) == 1                                  # [] asks for nothing
    async with env.db() as db:
        assert (await db.get(ChatSession, row.id)).tools is None


async def test_no_tools_never_fails_a_start(env):
    env.tools.fail = NoTools("no grant")
    row, _ = await env.start("chat")
    env.tools.fail = RuntimeError("tools-service down")
    row2, _ = await env.start("chat")
    for r in (row, row2):
        async with env.db() as db:
            assert (await db.get(ChatSession, r.id)).tools is None
    row3, _ = await env.start("chat", caller_token=None)              # e.g. a dev principal: no token
    assert len(env.tools.lists) == 2


async def test_a_restart_keeps_each_sessions_tools(env, db):
    row, _ = await env.start("chat")
    again = Sessions(db, EventLog(db), env.runner, dict(PROFILES), Settings(), env.tools)
    await again.load()
    assert again._live[row.id].tools == WEATHER


# ---- /mcp: paired like every gateway route (C7) -----------------------------------------------------
async def test_mcp_needs_the_sessions_capability_from_its_own_pod(env):
    async with env.serving():
        _, headers = await env.start("chat")
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.gateway), base_url="https://gateway.test") as h:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            assert (await h.post("/mcp", json=body)).status_code == 401
            stolen = {**headers, "x-sandbox-id": str(uuid.uuid4())}
            assert (await h.post("/mcp", json=body, headers=stolen)).status_code == 403


async def test_mcp_lists_the_sessions_tools(env):
    async with env.serving():
        _, headers = await env.start("chat")
        async with env.mcp(headers) as c:
            assert [t.name for t in (await c.list_tools()).tools] == ["weather_current", "weather_forecast"]
        _, headers = await env.start("toolless")
        async with env.mcp(headers) as c:
            assert (await c.list_tools()).tools == []


# ---- a call: the turn's caller's token, the list, the budget (TL4, TL7, TL10) --------------------------
async def test_a_call_uses_the_turns_caller_token(env):
    async with env.serving():
        row, headers = await env.start("tooled")
        turn, _ = await env.sessions.turn(row.id, ME, "weather?", caller_token="caller-of-this-turn")
        async with env.mcp(headers) as c:
            r = await c.call_tool("weather_forecast", {"place": "Seattle"})
        assert not r.is_error and r.structured_content == {"tool": "weather_forecast", "arguments": {"place": "Seattle"}}
        assert env.tools.calls == [("caller-of-this-turn", ["weather/"], "weather_forecast", {"place": "Seattle"})]
        async with env.db() as db:
            assert (await db.get(ChatSession, row.id)).tool_calls_used == 1


async def test_the_caller_token_goes_with_the_turn(env):
    async with env.serving():
        row, headers = await env.start("chat")
        turn, _ = await env.sessions.turn(row.id, ME, "hi", caller_token="t1")
        rt = Runtime(row.id)
        rt.seq = 1
        await env.sessions.ingest(row.id, rt.answer(turn.id))
        assert env.sessions._live[row.id].caller_token is None
        async with env.mcp(headers) as c:                                # between turns: no token, no call
            r = await c.call_tool("weather_current", {})
        assert r.is_error and env.tools.calls == []


async def test_a_tool_not_in_the_sessions_list_is_unknown(env):
    async with env.serving():
        row, headers = await env.start("chat")
        await env.sessions.turn(row.id, ME, "hi", caller_token="t1")
        async with env.mcp(headers) as c:
            with pytest.raises(MCPError, match="Unknown tool: home_unlock"):
                await c.call_tool("home_unlock", {})
        assert env.tools.calls == []


async def test_the_tool_budget_holds(env):
    async with env.serving():
        row, headers = await env.start("tooled")                         # max_tool_calls: 2
        await env.sessions.turn(row.id, ME, "hi", caller_token="t1")
        async with env.mcp(headers) as c:
            results = [await c.call_tool("weather_current", {}) for _ in range(3)]
        assert [r.is_error for r in results] == [False, False, True]
        assert "2 tool calls" in results[2].content[0].text and len(env.tools.calls) == 2


# ---- ToolsService: the exchange (TL4, TL5) -----------------------------------------------------------
class FakeTokens:
    def __init__(self, refuse=None):
        self.asked, self.refuse = [], refuse

    async def exchange(self, subject, audience, scopes=None, details=None):
        self.asked.append((subject, audience, scopes, details))
        if self.refuse:
            raise self.refuse
        return "tools-token"


async def test_the_exchange_asks_for_the_owners_grant_or_the_profiles_tools():
    tokens = FakeTokens()
    service = ToolsService(tokens, "https://tools.test/mcp")
    assert await service.token("caller", None) == "tools-token"
    assert await service.token("caller", ["weather/"]) == "tools-token"
    assert tokens.asked == [
        ("caller", "tools-service", ["tools:call"], None),
        ("caller", "tools-service", ["tools:call"],
         [{"type": "tool_access", "actions": ["call"], "locations": ["weather/"]}])]
    with pytest.raises(NoTools):
        await service.token("caller", [])
    assert len(tokens.asked) == 2


async def test_no_grant_is_no_tools_but_auth_service_down_is_an_error():
    with pytest.raises(NoTools):
        await ToolsService(FakeTokens(TokenRequestError("invalid_target", "no grant", 400)), "u").token("c", None)
    with pytest.raises(TokenRequestError):
        await ToolsService(FakeTokens(TokenRequestError("unavailable")), "u").token("c", None)


async def test_a_slow_tools_service_doesnt_hold_up_a_start(env):
    import asyncio

    async def slow(caller_token, mcp_tools):
        await asyncio.sleep(60)
    env.tools.list = slow
    env.sessions.s = Settings(TOOLS_START_TIMEOUT_SECONDS=0.2)
    row, _ = await asyncio.wait_for(env.start("chat"), 5)
    async with env.db() as db:
        assert (await db.get(ChatSession, row.id)).tools is None


async def test_mcp_waits_for_tools_still_being_looked_up(env):
    """The runtime lists its tools as soon as it starts, maybe before the start has them (5.4's race)."""
    import asyncio

    async def slow(caller_token, mcp_tools):
        await asyncio.sleep(0.4)
        return WEATHER
    env.tools.list = slow
    async with env.serving():
        start = asyncio.create_task(env.sessions.start(ME, "chat", caller_token="t"))
        while not env.runner.claims or not env.sessions._live:       # claimed and paired, tools not known yet
            await asyncio.sleep(0.01)
        claim = env.runner.claims[-1]
        assert not next(iter(env.sessions._live.values())).tools_ready.is_set()
        headers = {"authorization": f"Bearer {claim['capability']}", "x-sandbox-id": str(claim["sandbox_id"])}
        async with env.mcp(headers) as c:
            names = [t.name for t in (await c.list_tools()).tools]
        await start
    assert names == ["weather_current", "weather_forecast"]


def test_jti_of_reads_a_tokens_id():
    import base64
    import json

    from app.tools_service import jti_of
    body = base64.urlsafe_b64encode(json.dumps({"jti": "abc123"}).encode()).decode().rstrip("=")
    assert jti_of(f"h.{body}.s") == "abc123" and jti_of("not-a-jwt") is None
