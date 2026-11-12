"""dev-gateway: a temporary stand-in for agent-service's gateway, for milestone 1 (agent-runtime.md).

One session, one profile. It does the control plane's job just enough to test the runtime:

    at start      writes the session file (session id, capability, gateway URL, profile)
    runtime side  GET  /v1/runtime/turns     long poll: turns, interrupts, end      (capability)
                  POST /v1/runtime/events    events from the runtime                (capability)
                  ANY  /model/{path}         the model (OpenAI's Responses API): the capability is
                                             swapped for the real credential here (model-access.md):
                                               chatgpt  your subscription's token, from the auth broker
                                               apikey   an OpenAI API key (route D)
    your side     POST /dev/turns            {"text"}: streams that turn's events as NDJSON (dev token)
                  POST /dev/turns/{id}/interrupt,  POST /dev/end,  GET /dev/events?after=
                  GET  /dev/health           no auth: is the runtime ready?

Deleted when agent-service's gateway exists. Settings come from the environment (DEV_*).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import itertools
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from pydantic import ValidationError

from agent_dev.broker import AuthBroker, NotSignedIn
from core.types import EventBatch, EventType, Profile, SessionFile, parse_event

logger = logging.getLogger("agent_dev.gateway")

# Request headers the gateway never forwards upstream: the sandbox's credential and hop-by-hop ones.
DROP_HEADERS = {"host", "authorization", "x-api-key", "chatgpt-account-id", "content-length", "connection",
                "accept-encoding"}
# Response headers passed back to Codex: content, request ids and the plan's usage/rate-limit figures.
PASS_PREFIXES = ("content-type", "content-encoding", "retry-after", "x-request-id", "x-codex-", "x-ratelimit-", "openai-")
UPSTREAMS = {"chatgpt": "https://chatgpt.com/backend-api/codex", "apikey": "https://api.openai.com/v1"}


class Settings(BaseModel):
    profile_file: Path = Path(os.environ.get("DEV_PROFILE", "/profiles/general.yaml"))
    session_dir: Path = Path(os.environ.get("DEV_SESSION_DIR", "/session"))
    model_auth: Literal["chatgpt", "apikey"] = os.environ.get("DEV_MODEL_AUTH", "chatgpt")  # type: ignore[assignment]
    codex_home: Path = Path(os.environ.get("DEV_CODEX_HOME", "/secrets/codex-home"))      # chatgpt: the login
    api_key_file: Path = Path(os.environ.get("DEV_API_KEY_FILE", "/secrets/openai_api_key"))  # apikey
    dev_token_file: Path = Path(os.environ.get("DEV_TOKEN_FILE", "/secrets/dev_token"))
    gateway_url: str = os.environ.get("DEV_GATEWAY_URL", "http://dev-gateway:8080")   # as the sandbox sees it
    upstream: str | None = os.environ.get("DEV_UPSTREAM")                             # default: UPSTREAMS


class TurnRequest(BaseModel):
    text: str


class State:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session_id = "s_" + secrets.token_hex(6)
        self.capability = secrets.token_urlsafe(32)
        self.api_key = settings.api_key_file.read_text().strip() if settings.model_auth == "apikey" else None
        self.dev_token = settings.dev_token_file.read_text().strip()
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.events: list[dict[str, Any]] = []
        self.changed = asyncio.Condition()
        self.turn_ids = (f"t{n}" for n in itertools.count(1))
        self.ready = False

    def write_session_file(self) -> None:
        profile = Profile.model_validate(yaml.safe_load(self.settings.profile_file.read_text()))
        session = SessionFile(session_id=self.session_id, capability=self.capability,
                              gateway_url=self.settings.gateway_url, profile=profile)
        d = self.settings.session_dir
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "session.json.tmp"
        tmp.write_text(session.model_dump_json())
        tmp.chmod(0o444)                       # readable by the sandbox user (uid 10001), mounted read-only
        tmp.replace(d / "session.json")        # atomic: the runtime sees all of it or nothing

    async def add_events(self, raw_events: list[dict[str, Any]]) -> int:
        """Parse each event on its own (protocol A): an unknown or malformed one is dropped, not the batch."""
        accepted = []
        for raw in raw_events:
            try:
                accepted.append(parse_event(raw).model_dump(mode="json"))
            except ValidationError as exc:
                logger.warning("dropped an invalid event (type %r): %s", raw.get("type"), exc.errors()[:1])
        async with self.changed:
            self.events.extend(accepted)
            self.ready = self.ready or any(e["type"] == EventType.RUNTIME_READY for e in accepted)
            self.changed.notify_all()
        return len(raw_events) - len(accepted)


def create_app(settings: Settings | None = None, *, upstream_transport: httpx.AsyncBaseTransport | None = None,
               broker: AuthBroker | None = None) -> FastAPI:
    settings = settings or Settings()
    state = State(settings)
    upstream = httpx.AsyncClient(base_url=settings.upstream or UPSTREAMS[settings.model_auth],
                                 transport=upstream_transport, timeout=httpx.Timeout(30.0, read=600.0))
    if settings.model_auth == "chatgpt":
        broker = broker or AuthBroker(settings.codex_home)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.write_session_file()
        keeper = asyncio.create_task(broker.keep_fresh()) if broker else None
        yield
        if keeper:
            keeper.cancel()
        await upstream.aclose()

    app = FastAPI(title="dev-gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.dev = state

    def _presented(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")

    def runtime_auth(request: Request) -> None:
        if not hmac.compare_digest(_presented(request), state.capability):
            raise HTTPException(401, "invalid session capability")

    def dev_auth(request: Request) -> None:
        if not hmac.compare_digest(_presented(request), state.dev_token):
            raise HTTPException(401, "invalid dev token")

    # ---- the runtime's side ---------------------------------------------------------------------
    @app.get("/v1/runtime/turns", dependencies=[Depends(runtime_auth)])
    async def turns(wait: int = 25) -> dict:
        try:
            messages = [await asyncio.wait_for(state.inbox.get(), timeout=max(0, min(wait, 30)))]
        except TimeoutError:
            return {"messages": []}
        while not state.inbox.empty():
            messages.append(state.inbox.get_nowait())
        return {"messages": messages}

    @app.post("/v1/runtime/events", dependencies=[Depends(runtime_auth)])
    async def events_in(batch: EventBatch) -> dict:
        dropped = await state.add_events(batch.events)
        return {"ok": True, "dropped": dropped}

    def credential_headers() -> dict[str, str]:
        """The real credential, which exists only on this side."""
        if broker is None:
            return {"authorization": f"Bearer {state.api_key}"}
        token, account_id = broker.credentials()
        headers = {"authorization": f"Bearer {token}"}
        if account_id:
            headers["chatgpt-account-id"] = account_id
        return headers

    @app.api_route("/model/{path:path}", methods=["GET", "POST"], dependencies=[Depends(runtime_auth)])
    async def model(path: str, request: Request):
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_HEADERS}
        headers["accept-encoding"] = "identity"     # bytes pass through untouched
        body = await request.body()

        async def send() -> httpx.Response:
            req = upstream.build_request(request.method, f"/{path}", params=request.query_params,
                                         headers={**headers, **credential_headers()}, content=body)
            return await upstream.send(req, stream=True)

        try:
            resp = await send()
            if resp.status_code == 401 and broker is not None:     # expired: refresh once, then retry
                await resp.aclose()
                await broker.refresh()
                resp = await send()
        except NotSignedIn as exc:
            logger.error("%s", exc)
            return JSONResponse({"error": {"message": "the gateway has no model login"}}, status_code=503)
        logger.info("model %s /%s -> %s", request.method, path, resp.status_code)
        passthrough = {k: v for k, v in resp.headers.items() if k.lower().startswith(PASS_PREFIXES)}
        return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=passthrough,
                                 background=BackgroundTask(resp.aclose))

    # ---- your side -------------------------------------------------------------------------------
    @app.get("/dev/health")
    async def health() -> dict:
        return {"session_id": state.session_id, "runtime_ready": state.ready, "events": len(state.events)}

    @app.post("/dev/turns", dependencies=[Depends(dev_auth)])
    async def ask(body: TurnRequest):
        turn_id = next(state.turn_ids)
        start = len(state.events)
        await state.inbox.put({"type": "turn", "turn_id": turn_id, "text": body.text})

        async def stream():
            position = start
            yield json.dumps({"type": "dev.accepted", "turn_id": turn_id}) + "\n"
            while True:
                async with state.changed:
                    if position >= len(state.events):
                        try:
                            await asyncio.wait_for(state.changed.wait(), 15)
                        except TimeoutError:
                            pass
                    fresh = state.events[position:]
                position += len(fresh)
                if not fresh:
                    yield json.dumps({"type": "heartbeat"}) + "\n"
                for event in fresh:
                    if event.get("turn_id") == turn_id:
                        yield json.dumps(event) + "\n"
                        if event.get("type") == EventType.TURN_COMPLETED:
                            return

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/dev/turns/{turn_id}/interrupt", dependencies=[Depends(dev_auth)])
    async def interrupt(turn_id: str) -> dict:
        await state.inbox.put({"type": "interrupt", "turn_id": turn_id})
        return {"ok": True}

    @app.post("/dev/end", dependencies=[Depends(dev_auth)])
    async def end() -> dict:
        await state.inbox.put({"type": "end"})
        return {"ok": True}

    @app.get("/dev/events", dependencies=[Depends(dev_auth)])
    async def all_events(after: int = -1) -> JSONResponse:
        return JSONResponse([e for e in state.events if e.get("seq", -1) > after])

    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory
    logging.basicConfig(level=os.environ.get("AGENT_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return create_app()
