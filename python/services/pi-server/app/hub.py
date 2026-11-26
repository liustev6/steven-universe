"""
Connected hey-pi screens and sending to them.
"""

import logging
from dataclasses import dataclass

from fastapi import WebSocket
from pydantic import BaseModel

from app.protocol import encode

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Client:
    ws: WebSocket


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def add(self, ws: WebSocket) -> Client:
        client = Client(ws)
        self.clients.add(client)
        return client

    def remove(self, client: Client) -> None:
        self.clients.discard(client)

    async def send(self, client: Client, message: BaseModel) -> None:
        try:
            await client.ws.send_json(encode(message))
        except Exception as error:  # disconnected mid-send; the reader cleans up
            logger.debug("send failed: %s", error)

    async def broadcast(self, message: BaseModel) -> None:
        for client in list(self.clients):
            await self.send(client, message)
