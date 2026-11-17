"""/v1/agent/…: agent-service, for outside machine clients (mode `client`; control-plane.md "Callers").

    POST   /v1/agent/sessions                     chat:write   -> POST   /v1/sessions
    POST   /v1/agent/sessions/{id}/turns          chat:write   -> POST   /v1/sessions/{id}/turns     (SSE)
    GET    /v1/agent/sessions/{id}/events         chat:read    -> GET    /v1/sessions/{id}/events    (SSE)
    GET    /v1/agent/sessions/{id}                chat:read    -> GET    /v1/sessions/{id}
    POST   /v1/agent/sessions/{id}/cancel         chat:write   -> POST   /v1/sessions/{id}/cancel
    DELETE /v1/agent/sessions/{id}                chat:write   -> DELETE /v1/sessions/{id}
    GET    /v1/agent/conversations                chat:read    -> GET    /v1/conversations
    GET    /v1/agent/conversations/{id}/turns     chat:read    -> GET    /v1/conversations/{id}/turns

Not relayed: agent-service's kill switch and status page.

Per request: the caller's token (audience api-service) is verified and must carry the route's
scope, from a machine client calling as itself (not a user: no user login yet; not a token already
relayed by someone else) -> rate limit per client -> body limit -> the token is exchanged at
auth-service for one at agent-service (RFC 8693, grant 3): it carries the CALLER's own grants and
identity, never api-service's, so agent-service sees `service:<caller>`, owns its sessions by it,
and applies its scopes (E4: the caller's token itself never leaves api-service) -> forwarded ->
agent-service's answer streamed back unchanged: its status, its problem details, its event stream.
"""

from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.config import Settings
from app.edge import Mode, access
from app.ratelimit import RateLimiter

logger = logging.getLogger("api_service.agent")
AUDIENCE = "agent-service"
PASS_HEADERS = ("content-type", "retry-after", "x-turn-id")          # from agent-service's answer
STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def error(status: int, code: str, message: str, request: Request, headers: dict | None = None) -> JSONResponse:
    body = {"error": code, "message": message, "request_id": getattr(request.state, "request_id", None)}
    return JSONResponse(body, status_code=status, headers=headers)


def build_router(settings: Settings, http: httpx.AsyncClient, auth=None, tokens=None) -> APIRouter:
    """`auth`: service-auth's ServiceAuth for audience api-service; `tokens`: its TokenClient (for
    the exchange). Both from the environment unless given (tests)."""
    if auth is None:
        from service_auth.fastapi_auth import ServiceAuth

        auth = ServiceAuth.from_env("api-service")          # SERVICE_AUTH_ISSUER
    if tokens is None:
        from service_auth import TokenClient

        tokens = TokenClient.from_env()                     # api-service's own client: may relay (grant 3)
    router = APIRouter(prefix="/v1/agent")
    base = settings.AGENT_URL.rstrip("/")
    limiter = RateLimiter(settings.AGENT_RATE_BURST, settings.AGENT_RATE_PER_MINUTE)
    client_mode = access(Mode.CLIENT)

    async def relay(request: Request, principal, method: str, path: str, params: dict | None = None):
        if principal.on_behalf_of is not None or principal.actor_chain or not principal.subject.startswith("service:"):
            return error(403, "forbidden", "only machine clients calling as themselves", request)
        who = principal.subject
        if not limiter.allow(who):
            return error(429, "rate_limited", "too many agent requests", request,
                         {"Retry-After": str(limiter.retry_after(who))})
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > settings.AGENT_MAX_BODY_BYTES:
            return error(413, "too_large", "request body too large", request)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > settings.AGENT_MAX_BODY_BYTES:
                return error(413, "too_large", "request body too large", request)

        headers = {"X-Request-ID": request.state.request_id}
        if body:
            headers["Content-Type"] = "application/json"
        if last := request.headers.get("last-event-id"):
            headers["Last-Event-ID"] = last
        upstream = http.build_request(method, base + path, params=params, content=body or None, headers=headers)
        try:
            response = await http.send(upstream, stream=True, auth=tokens.auth_for(principal, AUDIENCE))
        except httpx.HTTPError as exc:
            logger.warning("agent-service unreachable: %r", exc)
            return error(502, "bad_gateway", "agent-service is unavailable", request)
        except Exception as exc:                 # the exchange was refused: no grant at agent-service
            logger.info("agent relay for %s refused at the exchange: %s", who, exc)
            return error(403, "forbidden", "this client has no access to agents", request)
        logger.info("agent %s %s for %s: %d", method, path, who, response.status_code)
        passed = {k: v for k, v in response.headers.items() if k.lower() in PASS_HEADERS}
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
                                 headers={**passed, **STREAM_HEADERS}, background=BackgroundTask(response.aclose))

    write, read = auth.require("chat:write"), auth.require("chat:read")

    @router.post("/sessions", dependencies=[Depends(client_mode)])
    async def start(request: Request, p=Depends(write)):
        return await relay(request, p, "POST", "/v1/sessions")

    @router.post("/sessions/{session_id}/turns", dependencies=[Depends(client_mode)])
    async def turn(session_id: uuid.UUID, request: Request, p=Depends(write)):
        return await relay(request, p, "POST", f"/v1/sessions/{session_id}/turns")

    @router.get("/sessions/{session_id}/events", dependencies=[Depends(client_mode)])
    async def events(session_id: uuid.UUID, request: Request, after: int = -1, p=Depends(read)):
        return await relay(request, p, "GET", f"/v1/sessions/{session_id}/events", {"after": after})

    @router.get("/sessions/{session_id}", dependencies=[Depends(client_mode)])
    async def get(session_id: uuid.UUID, request: Request, p=Depends(read)):
        return await relay(request, p, "GET", f"/v1/sessions/{session_id}")

    @router.post("/sessions/{session_id}/cancel", dependencies=[Depends(client_mode)])
    async def cancel(session_id: uuid.UUID, request: Request, p=Depends(write)):
        return await relay(request, p, "POST", f"/v1/sessions/{session_id}/cancel")

    @router.delete("/sessions/{session_id}", dependencies=[Depends(client_mode)])
    async def end(session_id: uuid.UUID, request: Request, p=Depends(write)):
        return await relay(request, p, "DELETE", f"/v1/sessions/{session_id}")

    @router.get("/conversations", dependencies=[Depends(client_mode)])
    async def conversations(request: Request, limit: int = 50, p=Depends(read)):
        return await relay(request, p, "GET", "/v1/conversations", {"limit": limit})

    @router.get("/conversations/{conversation_id}/turns", dependencies=[Depends(client_mode)])
    async def turns(conversation_id: uuid.UUID, request: Request, limit: int = 100, events: bool = False,
                    p=Depends(read)):
        return await relay(request, p, "GET", f"/v1/conversations/{conversation_id}/turns",
                           {"limit": limit, "events": str(events).lower()})

    return router
