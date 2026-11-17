"""The gateway (port 8443): the only thing a sandbox can reach (control-plane.md C1, "The gateway").

    GET  /v1/runtime/turns?wait=   the runtime's long poll: turns, interrupts, end
    POST /v1/runtime/events        protocol A events → translated, stored, sent on (the sessions core)
    ANY  /model/{path}             to OpenAI's Responses API with your login (C11), within budget (C13)

Every request, before anything else (C7):
- `Authorization: Bearer <capability>` must belong to a live session (else 401);
- `X-Sandbox-Id`, stamped by the pod's own egress proxy, must be that session's pod (else 403,
  logged): a capability copied to another sandbox is useless there.

The model route is a pass-through with four jobs: swap the capability for the real credential;
enforce the profile's model; refuse once the running turn is over its token budget; and count the
tokens of every response it relays (the `usage` of the Responses API's final event). It counts
what OpenAI says, never what the sandbox says, so a compromised sandbox can't spend past its budget.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask

from app import events as b
from app.broker import ModelLogin, NotSignedIn
from app.sessions import Live, Sessions
from core.types import EventBatch

logger = logging.getLogger(__name__)

UPSTREAM = "https://chatgpt.com/backend-api/codex"     # your plan's endpoint (model-access.md)
EVENTS_MAX_BYTES = 1 << 20                              # one batch of events
MODEL_MAX_BYTES = 16 << 20                              # one model request (the whole context)
# Never forwarded upstream: the sandbox's credential, the proxy's stamp, and hop-by-hop headers.
DROP_HEADERS = {"host", "authorization", "x-api-key", "chatgpt-account-id", "content-length", "connection",
                "accept-encoding", "x-sandbox-id", "x-forwarded-for", "x-real-ip"}
# Passed back to Codex: content, request ids, and the plan's usage and rate-limit figures.
PASS_PREFIXES = ("content-type", "content-encoding", "retry-after", "x-request-id", "x-codex-", "x-ratelimit-",
                 "openai-")
FINAL_EVENTS = (b"response.completed", b"response.incomplete", b"response.failed")


def create_gateway(sessions: Sessions, login: ModelLogin, upstream: httpx.AsyncClient) -> FastAPI:
    app = FastAPI(title="agent-service gateway", docs_url=None, redoc_url=None, openapi_url=None)

    def runtime(request: Request) -> Live:
        auth = request.headers.get("authorization", "")
        capability = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        live = sessions.live_for(capability) if capability else None
        if live is None:
            raise HTTPException(401, "invalid session capability")
        stamp = request.headers.get("x-sandbox-id", "")
        if not hmac.compare_digest(stamp, str(live.sandbox_id)):
            logger.warning("session %s: its capability was used from another sandbox (%r): refused",
                           live.session_id, stamp[:64])
            raise HTTPException(403, "this capability isn't valid from this sandbox")
        return live

    # ---- the runtime's side -------------------------------------------------------------------------
    @app.get("/v1/runtime/turns")
    async def turns(wait: float = 25, live: Live = Depends(runtime)) -> dict:
        return {"messages": await sessions.next_messages(live.session_id, wait)}

    @app.post("/v1/runtime/events")
    async def events_in(request: Request, live: Live = Depends(runtime)) -> dict:
        body = await _read(request, EVENTS_MAX_BYTES)
        try:
            batch = EventBatch.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "not an event batch")
        dropped = await sessions.ingest(live.session_id, batch.events)
        return {"ok": True, "dropped": dropped}

    # ---- the model ----------------------------------------------------------------------------------
    @app.api_route("/model/{path:path}", methods=["GET", "POST"])
    async def model(path: str, request: Request, live: Live = Depends(runtime)):
        profile = sessions.profiles[live.profile]
        if live.turn_tokens >= profile.limits.max_turn_tokens:
            await _over_budget(live, profile.limits.max_turn_tokens)
            return _refuse(403, "budget_exceeded",
                           f"this turn has used its {profile.limits.max_turn_tokens} tokens (the profile's budget)")
        body = await _read(request, MODEL_MAX_BYTES)
        wrong = _wrong_model(request, body, profile.model)
        if wrong is not None:
            logger.warning("session %s: model %r refused (the profile allows %r)", live.session_id, wrong,
                           profile.model)
            return _refuse(403, "model_not_allowed", f"this session may only use {profile.model}")

        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_HEADERS}
        headers["accept-encoding"] = "identity"      # plain bytes back: relayed untouched, and readable

        async def send() -> httpx.Response:
            req = upstream.build_request(request.method, f"/{path}", params=request.query_params,
                                         headers={**headers, **login.headers()}, content=body)
            return await upstream.send(req, stream=True)

        try:
            resp = await send()
            if resp.status_code == 401:              # the login expired: refresh once, then retry
                await resp.aclose()
                await login.refresh()
                resp = await send()
        except NotSignedIn as exc:
            logger.error("model route: %s", exc)
            return _refuse(503, "no_login", "the gateway has no model login")
        except httpx.HTTPError as exc:
            logger.warning("model route: upstream unreachable (%r)", exc)
            return _refuse(502, "upstream_unreachable", "the model provider couldn't be reached")

        counter = UsageCounter()

        async def relay():
            try:
                async for chunk in resp.aiter_raw():
                    counter.feed(chunk)
                    yield chunk
            finally:
                live.turn_tokens += counter.tokens
                logger.info("session %s: model %s /%s -> %s (%d tokens; turn %d/%d)", live.session_id,
                            request.method, path, resp.status_code, counter.tokens, live.turn_tokens,
                            profile.limits.max_turn_tokens)

        passthrough = {k: v for k, v in resp.headers.items() if k.lower().startswith(PASS_PREFIXES)}
        return StreamingResponse(relay(), status_code=resp.status_code, headers=passthrough,
                                 background=BackgroundTask(resp.aclose))

    async def _over_budget(live: Live, limit: int) -> None:
        """Tell the caller once per turn (an `error` event); the runtime's turn then fails on the 403."""
        if live.over_budget:
            return
        live.over_budget = True
        logger.warning("session %s: turn over its token budget (%d)", live.session_id, limit)
        await sessions.log.append(live.session_id, [b.new_event(
            b.Error(code=b.ErrorCode.BUDGET, message=f"this turn used its {limit} tokens"), turn_id=live.turn_id)])

    return app


# ---- helpers --------------------------------------------------------------------------------------
async def _read(request: Request, limit: int) -> bytes:
    """The body, refusing (413) past `limit` without reading the rest into memory."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "request too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _wrong_model(request: Request, body: bytes, allowed: str | None) -> str | None:
    """The model a request asks for, if the profile pins another one. Fails closed: a body the
    gateway can't read (compressed, not JSON) counts as wrong when a model is pinned."""
    if allowed is None or request.method != "POST" or not body:
        return None
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        return "(unreadable: compressed)"
    try:
        asked = json.loads(body).get("model")
    except (ValueError, AttributeError):
        return "(unreadable)"
    return None if asked == allowed else str(asked)


def _refuse(status: int, kind: str, message: str) -> JSONResponse:
    """An error in OpenAI's shape, so Codex shows the message. 403, not 429: Codex retries a 429."""
    return JSONResponse({"error": {"type": kind, "message": message}}, status_code=status)


class UsageCounter:
    """Reads the Responses API's server-sent events as they pass and adds up the `usage` of each
    response's final event. The bytes themselves are relayed untouched."""

    def __init__(self) -> None:
        self.tokens = 0
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            if line.startswith(b"data:") and any(e in line for e in FINAL_EVENTS):
                self._count(line[5:])

    def _count(self, data: bytes) -> None:
        try:
            event: dict[str, Any] = json.loads(data)
            usage = (event.get("response") or {}).get("usage") or {}
            self.tokens += int(usage.get("total_tokens") or 0)
        except (ValueError, TypeError, AttributeError):
            pass
