"""MCP (host 8443), for agents: the gateway (5.3) and MCP clients such as Claude Code (tools.md TL13).

The MCP SDK's own resource-server auth (the MCP authorization spec): a bearer token on every request,
checked by service-auth (signature, issuer, `aud=tools-service`), `tools:call` required, 401 with the
protected-resource metadata (RFC 9728) otherwise. Stateless Streamable HTTP with JSON answers: each
request stands alone, so the list and every call are decided by that request's token.

    tools/list   the tools this token may call (TL6, TL7): a model never sees one it can't use
    tools/call   the same path as the API (app.registry.call); a tool not granted is answered as an
                 unknown tool (no hint that it exists) and audited as a security event
"""

from __future__ import annotations

import json

from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types.jsonrpc import INVALID_PARAMS
from pydantic import ValidationError
from service_auth import AuthError, Principal
from service_auth.verifier import TokenVerifier

from app.access import SCOPE
from app.registry import Denied, Registry, Tool, ToolError, call

AUDIENCE = "tools-service"
INSTRUCTIONS = "Tools for the home's agents. Each caller sees only the tools its token grants."


class Verifier:
    """The SDK's TokenVerifier, backed by service-auth's: the Principal rides along in `claims`."""

    def __init__(self, verifier: TokenVerifier):
        self.verifier = verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            p = await self.verifier.verify(token)
        except AuthError:
            return None
        return AccessToken(token=token, client_id=p.client_id or p.service, scopes=sorted(p.scopes),
                           resource=AUDIENCE, subject=p.subject, claims={"principal": p})


def _principal() -> Principal:
    token = get_access_token()
    if token is None or not token.claims or "principal" not in token.claims:   # the middleware guarantees it
        raise MCPError(INVALID_PARAMS, "not authenticated")
    return token.claims["principal"]


def _mcp_tool(tool: Tool) -> types.Tool:
    return types.Tool(
        name=tool.mcp_name, title=tool.title, description=tool.description,
        input_schema=tool.arguments.model_json_schema(),
        annotations=types.ToolAnnotations(title=tool.title, read_only_hint=tool.read_only,
                                          destructive_hint=tool.destructive, open_world_hint=False),
    )


def _text(text: str, *, error: bool = False, structured: dict | None = None) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=error,
                                structured_content=structured)


def create_mcp(registry: Registry, verifier: TokenVerifier, *, issuer: str, resource_url: str,
               allowed_hosts: list[str], revision: str):
    """The MCP server and its ASGI app. The caller runs `server.session_manager.run()` around serving."""

    async def list_tools(ctx: ServerRequestContext, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[_mcp_tool(t) for t in registry.visible_to(_principal())])

    async def call_tool(ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = registry.by_mcp_name(params.name)
        location = tool.location if tool else params.name
        try:
            result = await call(registry, location, _principal(), dict(params.arguments or {}), interface="mcp")
        except Denied:
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name}") from None
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in e.errors())
            return _text(f"Invalid arguments: {problems}", error=True)
        except ToolError as e:
            return _text(e.message, error=True)
        return _text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), structured=result)

    server = Server("tools-service", version=revision, instructions=INSTRUCTIONS,
                    on_list_tools=list_tools, on_call_tool=call_tool)
    app = server.streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0",
        auth=AuthSettings(issuer_url=issuer, resource_server_url=resource_url, required_scopes=[SCOPE],
                          validate_token_resource=False),          # service-auth checks `aud` itself
        token_verifier=Verifier(verifier),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                     allowed_hosts=allowed_hosts, allowed_origins=[]),
    )
    return server, app
