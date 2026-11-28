"""tools-service, as agent-service reaches it for its sessions (docs/specs/agent-service/tools.md).

Every request carries a token exchanged for the session's caller (TL4: RFC 8693, auth-service's
grant 3): `sub` = the caller, `act` = agent-service, the tools = the caller's grant at tools-service,
narrowed by the profile's `mcp_tools` when it has them (TL5). The caller's own token (audience
agent-service) is what gets exchanged; neither token ever reaches the sandbox (TL3).

    list(caller_token, mcp_tools)                 at a session's start: its tools, as tools-service lists them
    call(caller_token, mcp_tools, name, args)     a tool call, forwarded as it came
"""

from __future__ import annotations

import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import httpx2
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from service_auth import TokenClient, TokenRequestError

logger = logging.getLogger(__name__)
AUDIENCE = "tools-service"
SCOPE = "tools:call"


def tool_instructions(tools: list[dict[str, Any]], server: str = "tools") -> str:
    """The session's tools, for its instructions (always, when it has any; the list tools-service gave
    for the exchanged token: after the intersection, never the profile's request): each by the
    name the model sees (Codex: `mcp__<server>__<tool>`) and its description's first sentence. Sorted,
    so the same grants always give the same text (the prompt cache)."""
    if not tools:
        return ""
    lines = ["Your tools for this session. Prefer them over a web search whenever one fits; if you "
             "don't see one yet, look it up with your tool search:"]
    for t in sorted(tools, key=lambda t: t.get("name", "")):
        description = " ".join((t.get("description") or "").split())
        first = description.split(". ")[0].rstrip(".")[:200]
        lines.append(f"- mcp__{server}__{t.get('name')}: {first}.")
    return "\n".join(lines)


def jti_of(token: str) -> str | None:
    """A token's `jti`, for log lines only (it's auth-service's token, just minted; not a verification)."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("jti")
    except (IndexError, ValueError):
        return None


class NoTools(Exception):
    """The caller may use no tools here: no grant at tools-service, or none the profile wants."""


class ToolsService:
    def __init__(self, tokens: TokenClient, url: str, *, timeout: float = 30.0,
                 client: Callable[[str], Any] | None = None):
        self.tokens, self.url, self.timeout = tokens, url, timeout
        self._client = client or self._http_client          # tests: an in-memory MCP client

    def _http_client(self, token: str):
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
        return Client(streamable_http_client(self.url, http_client=http))

    async def token(self, caller_token: str, mcp_tools: list[str] | None) -> str:
        """The caller's tools-service token. Raises NoTools when the caller has nothing there."""
        if mcp_tools == []:
            raise NoTools("the profile takes no tools-service tools")
        details = None if mcp_tools is None else [
            {"type": "tool_access", "actions": ["call"], "locations": list(mcp_tools)}]
        try:
            return await self.tokens.exchange(caller_token, AUDIENCE, scopes=[SCOPE], details=details)
        except TokenRequestError as e:
            if e.transient:
                raise
            raise NoTools(str(e)) from e                     # no grant, or nothing in common (TL5)

    @asynccontextmanager
    async def _session(self, caller_token: str, mcp_tools: list[str] | None) -> AsyncIterator[Client]:
        async with self._client(await self.token(caller_token, mcp_tools)) as client:
            yield client

    async def list(self, caller_token: str, mcp_tools: list[str] | None) -> list[dict[str, Any]]:
        """The tools this caller may use with this profile, as MCP tool definitions (JSON)."""
        async with self._session(caller_token, mcp_tools) as client:
            result = await client.list_tools()
        return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in result.tools]

    async def call(self, caller_token: str, mcp_tools: list[str] | None, name: str,
                   arguments: dict[str, Any]) -> tuple[types.CallToolResult, str | None]:
        """The result, and the exchanged token's `jti` (for the gateway's log line)."""
        token = await self.token(caller_token, mcp_tools)
        async with self._client(token) as client:
            return await client.call_tool(name, arguments), jti_of(token)
