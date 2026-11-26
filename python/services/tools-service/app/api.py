"""The REST API (host 443), for apps: Hey Pi's dashboard through pi-server, the phone through
api-service (tools.md TL13). The same tools, check and audit as MCP (app.registry.call).

    GET /health                                   liveness (no token)
    GET /v1/tools                                 the tools this token may call
    GET /v1/weather/current?place=…               weather/current
    GET /v1/weather/forecast?place=…&days=…       weather/forecast

Every route but /health needs a token for tools-service with `tools:call`; each tool route also needs
its tool granted (403 otherwise). Errors are RFC 9457 problem details.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from service_auth import Principal
from service_auth.fastapi_auth import ServiceAuth

from app.access import SCOPE
from app.registry import Denied, Registry, Tool, ToolError, call


def problem(status: int, title: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse({"type": "about:blank", "title": title, "status": status, "detail": detail, **extra},
                        status_code=status, media_type="application/problem+json")


def describe(tool: Tool) -> dict:
    return {"name": tool.location, "mcp_name": tool.mcp_name, "title": tool.title, "description": tool.description,
            "read_only": tool.read_only, "api": tool.api_path, "arguments": tool.arguments.model_json_schema()}


def create_api(registry: Registry, auth: ServiceAuth, *, revision: str) -> FastAPI:
    app = FastAPI(title="tools-service", docs_url=None, redoc_url=None, openapi_url=None)
    caller = auth.require(SCOPE)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "version": revision, "tools": len(registry)}

    @app.get("/v1/tools")
    async def tools(p: Principal = Depends(caller)):
        return {"tools": [describe(t) for t in registry.visible_to(p)]}

    def route(tool: Tool):
        async def handler(request: Request, p: Principal = Depends(caller)):
            try:
                return await call(registry, tool.location, p, dict(request.query_params), interface="api")
            except Denied:
                return problem(403, "Forbidden", f"this token may not call {tool.location}")
            except ValidationError as e:
                return problem(400, "Bad Request", "invalid arguments",
                               errors=[{"field": ".".join(map(str, err["loc"])), "message": err["msg"]} for err in e.errors()])
            except ToolError as e:
                return problem(e.status, "Tool failed", e.message)
        return handler

    for tool in registry:
        if tool.api_path:
            app.add_api_route(tool.api_path, route(tool), methods=["GET"], name=tool.location)
    return app
