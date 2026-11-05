from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    # Weather pushes effectively off; assistant replies instant.
    return Settings(
        DATA_DIR=tmp_path,
        MOCK_WEATHER_INTERVAL=3600,
        MOCK_REPLY_WORD_DELAY=0,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def receive_until(ws, message_type, limit=200):
    """Read messages until one of [message_type]; returns it."""
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] == message_type:
            return message
    raise AssertionError(f"no {message_type} within {limit} messages")


@contextmanager
def connect(client, feeds=("weather",)):
    """Open /ws and do the hello -> subscribe handshake; yields (ws, snapshot).
    Closes the socket on exit (an unclosed test socket hangs TestClient)."""
    with client.websocket_connect("/ws") as ws:
        receive_until(ws, "settings")
        ws.send_json({"type": "hello", "protocol_version": 1, "client": "test"})
        ws.send_json({"type": "subscribe", "feeds": list(feeds)})
        yield ws, receive_until(ws, "snapshot")
