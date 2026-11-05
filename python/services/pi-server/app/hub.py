"""
Connected hey-pi clients and sending to them.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket
from pydantic import BaseModel

from app.protocol import encode

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class Client:
    ws: WebSocket
    name: str = "unknown"
    # Feeds this client subscribed to, e.g. {"weather", "ha:light.kitchen"}.
    feeds: set[str] = field(default_factory=set)


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
        except Exception as error:  # Disconnected mid-send; the reader cleans up.
            logger.debug("Send to %s failed: %s", client.name, error)

    async def broadcast(self, message: BaseModel, feed: Optional[str] = None) -> None:
        """Send to every client, or only to those subscribed to [feed]."""
        for client in list(self.clients):
            if feed is None or feed in client.feeds:
                await self.send(client, message)
