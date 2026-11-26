"""The protocol (app/protocol.py): `speech`, pi-server -> hey-pi; `wake`, hey-pi -> pi-server."""

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


def test_what_isnt_in_the_protocol_is_dropped_and_the_socket_stays(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("not json")
        ws.send_text("[1, 2]")
        ws.send_json({"type": "user_activity"})                     # the old protocol: gone
        ws.send_json({"type": "wake"})                              # the voice off: nothing to wake
        client.post("/debug/speech/listening")
        assert ws.receive_json() == {"type": "speech", "state": "listening"}


def test_the_voice_button_wakes_the_voice_assistant():
    server = PiServer(Settings(VOICE=False))
    woken = []

    class Voice:
        async def woken(self, by):
            woken.append(by)
    server.voice = Voice()

    async def go():
        await server.handle(None, '{"type": "wake"}')
        await server.handle(None, '{"type": "wake", "extra": 1}')   # extra fields: still a wake
        await server.handle(None, '{"type": "listen"}')             # not in the protocol
    asyncio.run(go())
    assert woken == ["the voice button", "the voice button"]


def test_the_voice_assistants_phases_become_the_speech_state():
    """idle -> idle; listening and thinking -> listening; speaking -> speaking (its voice heard)."""
    server = PiServer(Settings(VOICE=False))
    sent = []

    async def broadcast(message):
        sent.append(message.state)
    server.hub.broadcast = broadcast

    async def go():
        for phase in ("listening", "listening", "thinking", "speaking", "listening", "thinking", "idle"):
            await server.phase("v1", phase)
        await server.reply_delta("v1", "ignored")
        await server.awake()
    asyncio.run(go())
    assert sent == ["listening", "speaking", "listening", "idle"]


def test_voice_debug_endpoints_need_the_voice(client):
    assert client.post("/debug/voice/listen").status_code == 409
    assert client.post("/debug/say", params={"text": "hi"}).status_code == 409
    assert client.post("/debug/speech/thinking").status_code == 422   # not a speech state
