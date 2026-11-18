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
It uses the service's own .venv (FastAPI, uvicorn, httpx, websockets, service-auth: requirements-dev.txt).
Options: --service URL, --issuer URL, --client ID, --secret FILE, --port N.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from pathlib import Path

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from service_auth import TokenClient

ap = argparse.ArgumentParser()
ap.add_argument("--service", default="https://speech.lan.liustev6.ca")
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


if __name__ == "__main__":
    print(f"http://localhost:{args.port}  →  {args.service} as {args.client}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
