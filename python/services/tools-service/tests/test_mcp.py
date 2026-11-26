"""MCP: the SDK's client against the app, as Claude Code or the gateway would use it."""

import json
import logging

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from tests.conftest import MCP_URL, bearer


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def session(mcp, token: str | None):
    server, app = mcp
    headers = bearer(token) if token else {}
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers, base_url="http://testserver")
    return server, Client(streamable_http_client(MCP_URL, http_client=http))


@pytest.mark.anyio
async def test_the_list_is_what_the_token_grants(mcp, issuer):
    server, pi = await session(mcp, issuer.token("pi-server", ["weather/", "pi/"]))
    _, smoke = await session(mcp, issuer.token("smoke-test", ["weather/forecast"]))
    async with server.session_manager.run():
        async with pi:
            tools = (await pi.list_tools()).tools
        async with smoke:
            assert [t.name for t in (await smoke.list_tools()).tools] == ["weather_forecast"]
    assert [t.name for t in tools] == ["weather_current", "weather_forecast", "pi_end_conversation"]
    forecast = tools[1]
    assert forecast.annotations.read_only_hint is True
    assert forecast.input_schema["properties"]["days"]["maximum"] == 7
    assert forecast.input_schema["additionalProperties"] is False


@pytest.mark.anyio
async def test_a_call_returns_structured_weather(mcp, issuer):
    server, client = await session(mcp, issuer.token("pi-server", ["weather/"]))
    async with server.session_manager.run(), client:
        r = await client.call_tool("weather_forecast", {"place": "Seattle", "days": 2})
    assert not r.is_error
    assert r.structured_content["days"][1]["condition"] == "windy"
    assert json.loads(r.content[0].text) == r.structured_content


@pytest.mark.anyio
async def test_a_tool_not_granted_is_an_unknown_tool(mcp, issuer, caplog):
    server, client = await session(mcp, issuer.token("smoke-test", ["weather/forecast"]))
    with caplog.at_level(logging.INFO, logger="tools_service.audit"):
        async with server.session_manager.run(), client:
            with pytest.raises(MCPError, match="Unknown tool: weather_current"):
                await client.call_tool("weather_current", {"place": "Seattle"})
            with pytest.raises(MCPError, match="Unknown tool: no_such_tool"):
                await client.call_tool("no_such_tool", {})
    denied = [json.loads(r.getMessage()) for r in caplog.records]
    assert [(d["tool"], d["outcome"], d["known"]) for d in denied] == [
        ("weather/current", "denied", True), ("no_such_tool", "denied", False)]


@pytest.mark.anyio
async def test_tool_errors_come_back_as_results_the_model_can_read(mcp, issuer):
    server, client = await session(mcp, issuer.token("pi-server", ["weather/"]))
    async with server.session_manager.run(), client:
        bad = await client.call_tool("weather_forecast", {"place": "Seattle", "days": 30})
        nowhere = await client.call_tool("weather_current", {"place": "Atlantis"})
    assert bad.is_error and "days" in bad.content[0].text
    assert nowhere.is_error and "Atlantis" in nowhere.content[0].text


@pytest.mark.anyio
async def test_end_conversation_is_acknowledged(mcp, issuer):
    server, client = await session(mcp, issuer.token("pi-server", ["pi/"]))
    async with server.session_manager.run(), client:
        r = await client.call_tool("pi_end_conversation", {})
    assert r.structured_content == {"ending": True}


@pytest.mark.anyio
async def test_no_token_or_a_bad_one_is_401_with_the_resource_metadata(mcp, issuer):
    server, app = mcp
    async with server.session_manager.run():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as h:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            r = await h.post("/mcp", json=body)
            assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]
            wrong = issuer.token("pi-server", ["weather/"], aud="agent-service")
            assert (await h.post("/mcp", json=body, headers=bearer(wrong))).status_code == 401
            meta = (await h.get("/.well-known/oauth-protected-resource/mcp")).json()
            assert meta["authorization_servers"] == ["https://auth.test"] and meta["scopes_supported"] == ["tools:call"]
