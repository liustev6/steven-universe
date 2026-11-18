"""speech-service's test client: a page to talk to the live service (speech to text) and hear it
(text to speech), as the `speech-test-client` auth client.

    cd python/services/speech-service
    .venv/bin/python test-client/server.py            # then open http://localhost:8765

The page talks only to this server (localhost). This server gets a token as speech-test-client
(auth-service, client credentials; its secret in ~/.config/speech-test-client/secret, mode 600) and:
  - forwards each WebSocket session to the service with `Authorization: Bearer …` (browsers can't set
    that header on a WebSocket), passing messages both ways unchanged, and the service's close code;
    a handshake the service refuses (503 busy, 401…) reaches the page as an `error` message
  - forwards /v1/voices and /v1/status with the token
  - runs the voice loop (/voice): speech → agent-service (profile voice) → speech, as pi-server will
It uses the service's own .venv (FastAPI, uvicorn, httpx, websockets, service-auth: requirements-dev.txt).
Options: --service URL, --agent URL, --issuer URL, --client ID, --secret FILE, --port N.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from service_auth import TokenClient

ap = argparse.ArgumentParser()
ap.add_argument("--service", default="https://speech.lan.liustev6.ca")
ap.add_argument("--agent", default="https://agent.lan.liustev6.ca")
ap.add_argument("--issuer", default="https://auth.lan.liustev6.ca")
ap.add_argument("--client", default="speech-test-client")
ap.add_argument("--secret", default=str(Path.home() / ".config/speech-test-client/secret"))
ap.add_argument("--port", type=int, default=8765)
args = ap.parse_args()

PAGE = Path(__file__).with_name("index.html")
UPSTREAM_WS = args.service.replace("https", "wss", 1).replace("http", "ws", 1)
tokens = TokenClient(args.issuer, args.client, Path(args.secret).read_text().strip())
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


async def auth_header() -> dict[str, str]:
    return {"authorization": f"Bearer {await tokens.token('speech-service')}"}


@app.get("/")
async def page():
    return HTMLResponse(PAGE.read_text())


@app.get("/config")
async def config():
    return {"ws": f"ws://localhost:{args.port}", "service": args.service, "client": args.client}


@app.get("/v1/{path:path}")
async def forward(path: str):
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{args.service}/v1/{path}", headers=await auth_header())
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@app.websocket("/v1/realtime/{kind}")
async def session(ws: WebSocket, kind: str):
    """One session, forwarded: the page ⇄ this server ⇄ the service (with the token)."""
    await ws.accept()
    query = ws.url.query
    url = f"{UPSTREAM_WS}/v1/realtime/{kind}" + (f"?{query}" if query else "")
    try:
        up = await websockets.connect(url, additional_headers=await auth_header(), max_size=None)
    except websockets.exceptions.InvalidStatus as e:                 # refused at the handshake
        detail = e.response.body.decode(errors="replace")[:300] if e.response.body else ""
        await ws.send_text(json.dumps({"type": "error", "code": f"http_{e.response.status_code}",
                                       "message": f"the service refused: {e.response.status_code} {detail}"}))
        await ws.close(code=1013 if e.response.status_code == 503 else 1008)
        return

    async def to_page():
        async for message in up:
            if isinstance(message, bytes):
                await ws.send_bytes(message)
            else:
                await ws.send_text(message)
        with contextlib.suppress(Exception):
            await ws.close(code=up.close_code or 1000, reason=up.close_reason or "")

    async def to_service():
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await up.send(message["bytes"])
            elif message.get("text") is not None:
                await up.send(message["text"])
        await up.close()

    tasks = [asyncio.create_task(to_page()), asyncio.create_task(to_service())]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    with contextlib.suppress(Exception):
        await up.close()


# ---- the voice loop: speech → agent → speech ----------------------------------------------------------
# What pi-server will do, here first to prove each hop and time it. One /voice WebSocket per conversation:
#   page → server   binary: mic audio (16 kHz 16-bit mono)
#                   {"type": "listen"}  start a turn: a speech-to-text session (server turn detection)
#                   {"type": "commit"}  the user is done talking (when the silence didn't end it)
#                   {"type": "cancel"}  stop whatever runs: the listening, the agent's turn, the speech
#   server → page   {"type": "agent_session", ...}         the agent session (claimed as the page connects)
#                   {"from": "stt", ...}                    speech-service's transcription events (+ "from")
#                   {"from": "agent", ...}                  agent-service's protocol B events (+ "from")
#                   {"from": "tts", ...}                    speech-service's speech events (+ "from")
#                   binary: the reply's audio (24 kHz 16-bit mono)
#                   {"type": "reply_done", "timings": {…}}  times from the transcript's final (heard)
#                   {"type": "error", "where": ..., "message": ...}


class AgentError(Exception):
    pass


class Agent:
    """One conversation with agent-service (profile voice), as speech-test-client: its live session."""

    def __init__(self, http: httpx.AsyncClient, profile: str, conversation_id: str | None):
        self.http, self.profile = http, profile
        self.conversation_id, self.session_id = conversation_id, None

    async def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {await tokens.token('agent-service')}"}

    async def start(self) -> dict:
        body = {"profile": self.profile} | ({"conversation_id": self.conversation_id} if self.conversation_id else {})
        r = await self.http.post(f"{args.agent}/v1/sessions", json=body, headers=await self.headers())
        if r.status_code not in (200, 201):
            raise AgentError(f"starting a session: {r.status_code} {r.text[:300]}")
        d = r.json()
        self.session_id, self.conversation_id = d["session_id"], d["conversation_id"]
        return d

    async def turn(self, text: str):
        """The turn's protocol B events, as they come. A session that has ended (idle, its limits) is
        replaced once by a new one in the same conversation."""
        for attempt in range(2):
            if self.session_id is None:
                await self.start()
            url = f"{args.agent}/v1/sessions/{self.session_id}/turns"
            async with self.http.stream("POST", url, json={"text": text}, headers=await self.headers(),
                                        timeout=httpx.Timeout(15, read=180)) as r:
                if r.status_code in (404, 409) and attempt == 0:
                    self.session_id = None
                    continue
                if r.status_code != 200:
                    raise AgentError(f"turn: {r.status_code} {(await r.aread()).decode(errors='replace')[:300]}")
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[5:])
                return

    async def cancel(self) -> None:
        if self.session_id:
            with contextlib.suppress(Exception):
                await self.http.post(f"{args.agent}/v1/sessions/{self.session_id}/cancel", headers=await self.headers())


@app.websocket("/voice")
async def voice(ws: WebSocket):
    await ws.accept()
    q = ws.query_params
    sending = asyncio.Lock()

    async def send(obj: dict | bytes) -> None:
        async with sending:
            if isinstance(obj, bytes):
                await ws.send_bytes(obj)
            else:
                await ws.send_text(json.dumps(obj))

    async def problem(where: str, e: BaseException) -> None:
        if isinstance(e, websockets.exceptions.InvalidStatus):
            body = e.response.body.decode(errors="replace")[:300] if e.response.body else ""
            message = f"refused: {e.response.status_code} {body}"
        else:
            message = f"{type(e).__name__}: {e}"
        with contextlib.suppress(Exception):
            await send({"type": "error", "where": where, "message": message})

    http = httpx.AsyncClient(timeout=15)
    agent = Agent(http, q.get("profile", "voice"), q.get("conversation_id") or None)
    stt: websockets.ClientConnection | None = None
    tasks: set[asyncio.Task] = set()
    reply: asyncio.Task | None = None

    def spawn(coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        return t

    async def claim():                       # the session (and its pod) while the user is still talking
        try:
            started = time.monotonic()
            d = await agent.start()
            await send({"type": "agent_session", **d, "took_ms": round((time.monotonic() - started) * 1000)})
        except Exception as e:
            await problem("agent", e)

    async def listen():
        nonlocal stt, reply
        params = {"turn_detection": "server", "silence_ms": q.get("silence_ms", "700")}
        if q.get("stt_language"):
            params["language"] = q["stt_language"]
        try:
            up = stt = await websockets.connect(f"{UPSTREAM_WS}/v1/realtime/transcription?{urlencode(params)}",
                                                additional_headers=await auth_header(), max_size=None)
            async for message in up:
                event = json.loads(message)
                await send({"from": "stt", **event})
                if event["type"] == "final":
                    if event["text"].strip():
                        reply = spawn(respond(event["text"], time.monotonic()))
                    else:
                        await send({"type": "reply_done", "timings": {}, "heard": ""})   # nothing said: listen again
        except Exception as e:
            await problem("stt", e)
        finally:
            if stt is not None and stt is up:
                stt = None

    async def respond(text: str, heard: float):
        """The agent's reply, streamed into speech as it's written."""
        timings: dict[str, int | None] = {}
        mark = lambda name: timings.setdefault(name, round((time.monotonic() - heard) * 1000))  # noqa: E731
        texts: asyncio.Queue[dict | None] = asyncio.Queue()           # text events for the speech session
        params = {"voice": q.get("voice", "ryan")} | ({"language": q["tts_language"]} if q.get("tts_language") else {})

        async def speak():
            async with websockets.connect(f"{UPSTREAM_WS}/v1/realtime/speech?{urlencode(params)}",
                                          additional_headers=await auth_header(), max_size=None) as up:
                mark("tts_connected")
                started = asyncio.Event()

                async def feed():
                    await started.wait()
                    while (item := await texts.get()) is not None:
                        await up.send(json.dumps(item))
                    await up.send(json.dumps({"type": "end"}))

                feeder = asyncio.create_task(feed())
                try:
                    async for message in up:
                        if isinstance(message, bytes):
                            mark("first_audio")
                            await send(message)
                            continue
                        event = json.loads(message)
                        await send({"from": "tts", **event})
                        if event["type"] == "started":
                            started.set()
                        if event["type"] == "done":
                            mark("audio_done")
                finally:
                    feeder.cancel()

        speaker = asyncio.create_task(speak())
        try:
            async for event in agent.turn(text):
                await send({"from": "agent", **event})
                kind = event.get("type")
                if kind == "message.delta" and event.get("text"):
                    mark("first_text")
                    texts.put_nowait({"type": "text", "text": event["text"]})
                elif kind == "message.completed":
                    texts.put_nowait({"type": "text", "text": " "})   # a gap between text blocks
                elif kind == "turn.completed":
                    mark("agent_done")
                    timings["agent_first_text_ms"] = event.get("first_text_ms")
                    break
            texts.put_nowait(None)
            await speaker
        except asyncio.CancelledError:
            speaker.cancel()
            raise
        except Exception as e:
            speaker.cancel()
            await problem("reply", e)
        await send({"type": "reply_done", "timings": timings, "heard": text})

    async def cancel_all():
        nonlocal reply
        if stt is not None:
            with contextlib.suppress(Exception):
                await stt.send(json.dumps({"type": "cancel"}))
        if reply is not None and not reply.done():
            reply.cancel()
            await agent.cancel()
        reply = None

    spawn(claim())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if stt is not None:
                    with contextlib.suppress(Exception):
                        await stt.send(message["bytes"])
                continue
            kind = json.loads(message["text"]).get("type")
            if kind == "listen" and stt is None:
                spawn(listen())
            elif kind == "commit" and stt is not None:
                with contextlib.suppress(Exception):
                    await stt.send(json.dumps({"type": "commit"}))
            elif kind == "cancel":
                await cancel_all()
    finally:
        await cancel_all()
        for t in list(tasks):
            t.cancel()
        await http.aclose()


if __name__ == "__main__":
    print(f"http://localhost:{args.port}  →  {args.service} as {args.client}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
