import json

from tests.conftest import connect, receive_until


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "healthy"
    assert body["clients"] == 0
    assert body["lab"] == "online"


def test_connect_sends_device_state_first(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "connection", "lab": "online"}
        assert ws.receive_json() == {"type": "settings", "theme_mode": "dark"}


def test_subscribe_returns_a_snapshot(client):
    with connect(client, feeds=["weather", "ha:light.kitchen"]) as (ws, snapshot):
        assert snapshot["protocol_version"] == 1
        assert snapshot["assistant"] == {"phase": "idle"}
        weather = snapshot["widgets"]["weather"]["data"]
        assert weather["location"] == {"name": "San Francisco"}
        assert set(weather["current"]) == {"temperature_c", "condition", "is_day"}
        assert weather["today"]["low_c"] < weather["current"]["temperature_c"]
        entity_ids = {s["entity_id"] for s in snapshot["ha_states"]}
        assert "light.kitchen" in entity_ids


def test_ha_toggle_answers_and_pushes_the_new_state(client):
    with connect(client, feeds=["ha:light.kitchen"]) as (ws, snapshot):
        before = next(s for s in snapshot["ha_states"] if s["entity_id"] == "light.kitchen")
        ws.send_json({
            "type": "ha_action", "id": "a1",
            "entity_id": "light.kitchen", "service": "light.toggle", "data": {},
        })
        assert receive_until(ws, "ha_action_result") == {
            "type": "ha_action_result", "id": "a1", "ok": True,
        }
        state = receive_until(ws, "ha_state")
        assert state["entity_id"] == "light.kitchen"
        assert state["state"] != before["state"]


def test_ha_action_on_unknown_entity_fails_cleanly(client):
    with connect(client) as (ws, _):
        ws.send_json({
            "type": "ha_action", "id": "a2",
            "entity_id": "light.nope", "service": "light.toggle",
        })
        result = receive_until(ws, "ha_action_result")
        assert result["ok"] is False
        assert "unknown entity" in result["error"]


def test_assistant_text_streams_a_reply(client):
    with connect(client) as (ws, _):
        ws.send_json({"type": "assistant_text", "text": "turn off the kitchen lights"})
        thinking = receive_until(ws, "assistant")
        assert thinking["phase"] == "thinking"
        assert thinking["transcript"] == "turn off the kitchen lights"

        reply = ""
        while True:
            message = receive_until(ws, "assistant")
            assert message["turn_id"] == thinking["turn_id"]
            if message["phase"] == "idle":
                break
            reply += message.get("reply_delta", "")
        assert "turn off the kitchen lights" in reply


def test_settings_update_is_saved_and_pushed(client, settings):
    with connect(client) as (ws, _):
        ws.send_json({"type": "settings_update", "theme_mode": "light"})
        assert receive_until(ws, "settings") == {"type": "settings", "theme_mode": "light"}
        saved = json.loads((settings.DATA_DIR / "settings.json").read_text())
        assert saved == {"theme_mode": "light"}

    # A new connection gets the saved setting.
    with client.websocket_connect("/ws") as ws2:
        assert receive_until(ws2, "settings")["theme_mode"] == "light"


def test_bad_messages_are_dropped_without_closing(client):
    with connect(client) as (ws, _):
        ws.send_text("not json")
        ws.send_json({"type": "no_such_message"})
        ws.send_json({"type": "ha_action"})  # missing fields
        # The connection still works.
        ws.send_json({"type": "subscribe", "feeds": []})
        assert receive_until(ws, "snapshot")["type"] == "snapshot"


def test_debug_endpoints_push_to_clients(client):
    with connect(client) as (ws, _):
        client.post("/debug/display/sleep")
        assert receive_until(ws, "display_state") == {"type": "display_state", "state": "sleep"}
        client.post("/debug/lab/offline")
        assert receive_until(ws, "connection") == {"type": "connection", "lab": "offline"}
        assert client.get("/health").json()["lab"] == "offline"
