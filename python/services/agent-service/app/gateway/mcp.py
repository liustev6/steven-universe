"""/mcp: the session's tools, for its sandbox (docs/specs/agent-service/tools.md TL2, TL7).

An MCP server (the MCP SDK's low-level Server, stateless Streamable HTTP, JSON answers) that Codex in
the sandbox uses as its one MCP server. Every request is paired like the runtime's (C7: the session's
capability, from its own pod), before the SDK sees it.

    tools/list   the session's tools, as tools-service listed them when it started (fixed: TL7)
    tools/call   the sessions core: the list, the budget, the turn's caller's token, tools-service
"""

from __future__ import annotations

import contextvars
import logging

from fastapi import HTTPException
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.gateway.pairing import pair
from app.sessions import Live, Sessions

logger = logging.getLogger(__name__)
_session: contextvars.ContextVar[Live] = contextvars.ContextVar("gateway_mcp_session")


class _Paired:
    """ASGI: pair the request with its session (401/403 otherwise), then hand it to the SDK."""

    def __init__(self, sessions: Sessions, app):
        self.sessions, self.app = sessions, app

    async def __call__(self, scope, receive, send):
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            live = pair(self.sessions, headers.get("authorization", ""), headers.get("x-sandbox-id", ""))
        except HTTPException as e:
            return await JSONResponse({"detail": e.detail}, status_code=e.status_code)(scope, receive, send)
        token = _session.set(live)
        try:
            await self.app(scope, receive, send)
        finally:
            _session.reset(token)


def create_mcp(sessions: Sessions) -> tuple[Server, Route]:
    """The MCP server and the gateway's /mcp route. Run `server.session_manager.run()` around serving."""

    async def list_tools(ctx: ServerRequestContext, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool.model_validate(t) for t in _session.get().tools])

    async def call_tool(ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await sessions.call_tool(_session.get(), params.name, dict(params.arguments or {}))

    server = Server("agent-service gateway", instructions="This session's tools.",
                    on_list_tools=list_tools, on_call_tool=call_tool)
    # The sandbox reaches the gateway only through its pod's proxy (no browser, no DNS rebinding);
    # the pairing above is the authentication.
    app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0",
                                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return server, Route("/mcp", endpoint=_Paired(sessions, app), methods=["GET", "POST", "DELETE"])
