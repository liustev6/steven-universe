"""
The WebSocket hey-pi connects to: ws://127.0.0.1:8765/ws
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hey-pi"])


@router.websocket("/ws")
async def hey_pi_socket(ws: WebSocket):
    server = ws.app.state.pi
    await ws.accept()
    client = server.hub.add(ws)
    logger.info("Client connected (%d total)", len(server.hub.clients))
    try:
        await server.on_connect(client)
        while True:
            await server.handle(client, await ws.receive_text())
    except WebSocketDisconnect:
        pass
    finally:
        server.hub.remove(client)
        logger.info("%s disconnected (%d left)", client.name, len(server.hub.clients))
