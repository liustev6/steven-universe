"""The protocol: one message, `speech`, pi-server -> hey-pi (app/protocol.py)."""

import asyncio

from app.core.config import Settings
from app.server import PiServer


def test_health(client):
    body = client.get("/health").json()
    assert body == {"status": "healthy", "version": "0.1.0", "clients": 0, "voice": False, "speech": "idle"}


def test_a_screen_is_told_the_speech_state_when_it_connects(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "speech", "state": "idle"}


def test_every_screen_hears_each_change_and_only_changes(client):
    with client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        a.receive_json(), b.receive_json()
        client.post("/debug/speech/listening")
        client.post("/debug/speech/listening")                      # no change: nothing sent
        client.post("/debug/speech/speaking")
        for ws in (a, b):
            assert ws.receive_json() == {"type": "speech", "state": "listening"}
            assert ws.receive_json() == {"type": "speech", "state": "speaking"}
        assert client.get("/health").json()["speech"] == "speaking"
    with client.websocket_connect("/ws") as late:                    # a new screen: the state now
        assert late.receive_json() == {"type": "speech", "state": "speaking"}


def test_whatever_a_screen_sends_is_dropped_and_the_socket_stays(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.send_json({"type": "user_activity"})                     # the old protocol: gone
        client.post("/debug/speech/listening")
        assert ws.receive_json() == {"type": "speech", "state": "listening"}


def test_the_voice_assistants_phases_become_the_speech_state():
    """idle -> idle; listening -> listening; thinking and speaking -> speaking (its turn)."""
    server = PiServer(Settings(VOICE=False))
    sent = []

    async def broadcast(message):
        sent.append(message.state)
    server.hub.broadcast = broadcast

    async def go():
        for phase in ("listening", "listening", "thinking", "speaking", "listening", "idle"):
            await server.phase("v1", phase)
        await server.reply_delta("v1", "ignored")
        await server.awake()
    asyncio.run(go())
    assert sent == ["listening", "speaking", "listening", "idle"]


def test_voice_debug_endpoints_need_the_voice(client):
    assert client.post("/debug/voice/listen").status_code == 409
    assert client.post("/debug/say", params={"text": "hi"}).status_code == 409
    assert client.post("/debug/speech/thinking").status_code == 422   # not a speech state
