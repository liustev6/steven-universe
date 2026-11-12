"""Protocol A (core/types), and SDK messages -> protocol-A payloads (the Translator)."""

import pytest
from openai_codex.generated.notification_registry import NOTIFICATION_MODELS
from openai_codex.models import Notification
from pydantic import ValidationError

from core.agent import CAPABILITY_ENV, Translator, codex_config, codex_overrides
from core.types import (
    OUTPUT_CHARS, EventType, InboundBatch, SessionFile, TextDelta, TurnCompleted, TurnStatus, build_event,
    event_json_schema, parse_event,
)

SESSION = {"session_id": "s_1", "capability": "cap-xyz", "gateway_url": "http://gw:8080/",
           "profile": {"name": "voice", "effort": "low", "instructions": "be brief"}}
ENV = {"session_id": "s_1", "turn_id": "t1", "seq": 3, "ts": 1.5}


# ---- the types (protocol A) ---------------------------------------------------------------------

def test_session_file_defaults_and_strictness():
    s = SessionFile.model_validate(SESSION)
    assert s.profile.model is None and s.profile.tools == [] and s.profile.limits.wall_seconds == 600
    with pytest.raises(ValidationError):
        SessionFile.model_validate({**SESSION, "api_key": "nope"})          # unknown fields refused
    with pytest.raises(ValidationError):
        SessionFile.model_validate({**SESSION, "profile": {"name": "x", "tools": ["rm_rf"]}})   # unknown tool


def test_inbound_messages_are_discriminated():
    b = InboundBatch.model_validate({"messages": [{"type": "turn", "turn_id": "t1", "text": "hi"},
                                                  {"type": "interrupt", "turn_id": "t1"}, {"type": "end"}]})
    assert [type(m).__name__ for m in b.messages] == ["TurnMessage", "InterruptMessage", "EndMessage"]


def test_build_event_adds_the_envelope_and_validates():
    e = build_event({"type": EventType.TEXT_DELTA, "text": "hi"}, **ENV)
    assert isinstance(e, TextDelta) and e.v == 1 and e.seq == 3
    assert e.model_dump(mode="json") == {"v": 1, "session_id": "s_1", "turn_id": "t1", "seq": 3, "ts": 1.5,
                                         "type": "text.delta", "text": "hi"}
    done = build_event({"type": "turn.completed", "status": "interrupted", "duration_ms": 9}, **ENV)
    assert isinstance(done, TurnCompleted) and done.status is TurnStatus.INTERRUPTED


@pytest.mark.parametrize("payload", [
    {"type": "text.delta"},                                       # missing field
    {"type": "text.delta", "text": "hi", "extra": 1},             # unknown field
    {"type": "turn.completed", "status": "done", "duration_ms": 1},   # not a TurnStatus
    {"type": "made.up"},                                          # unknown type
])
def test_malformed_events_are_refused(payload):
    with pytest.raises(ValidationError):
        build_event(payload, **ENV)


def test_parse_event_needs_the_envelope():
    with pytest.raises(ValidationError):
        parse_event({"type": "text.delta", "text": "hi"})
    assert parse_event({**ENV, "v": 1, "type": "text.delta", "text": "hi"}).text == "hi"


def test_every_event_type_has_a_model_in_the_schema():
    schema = event_json_schema()
    types = {d["properties"]["type"]["const"] for d in schema["$defs"].values() if "type" in d.get("properties", {})}
    assert types == {t.value for t in EventType}


def test_codex_points_at_the_gateway_with_only_the_capability(tmp_path):
    session = SessionFile.model_validate(SESSION)
    o = codex_overrides(session)
    assert o["model_provider"] == "gateway" and o["model_providers.gateway.base_url"] == "http://gw:8080/model"
    assert o["model_providers.gateway.env_key"] == CAPABILITY_ENV and o["model_providers.gateway.requires_openai_auth"] is False
    assert o["features.shell_tool"] is False and o["web_search"] == "disabled" and o["project_doc_max_bytes"] == 0
    cfg = codex_config(session, codex_home=str(tmp_path / "home"))
    assert cfg.env == {CAPABILITY_ENV: "cap-xyz", "CODEX_HOME": str(tmp_path / "home")}   # no real token anywhere
    assert 'model_providers.gateway.base_url="http://gw:8080/model"' in cfg.config_overrides
    assert "model_providers.gateway.requires_openai_auth=false" in cfg.config_overrides
    tools = codex_overrides(SessionFile.model_validate({**SESSION, "profile": {"name": "x", "tools": ["shell", "web_search"]}}))
    assert tools["features.shell_tool"] is True and tools["web_search"] == "live"


# ---- the Translator ------------------------------------------------------------------------------

def note(method, params):
    """A notification exactly as the SDK parses it off the wire (camelCase JSON-RPC params)."""
    if method == "item/started":
        params = {"startedAtMs": 0, **params}
    elif method == "item/completed":
        params = {"completedAtMs": 0, **params}
    return Notification(method=method, payload=NOTIFICATION_MODELS[method].model_validate(params))


IDS = {"threadId": "th_1", "turnId": "tu_1"}
TURN = {"id": "tu_1", "items": [], "error": None}


def valid(payloads):
    """Every payload the Translator produces must be a valid protocol-A event."""
    return [build_event(p, **ENV) for p in payloads]


def test_text_streams_then_completes():
    tr = Translator()
    out = []
    out += tr(note("item/agentMessage/delta", {**IDS, "itemId": "m1", "delta": "Hel"}))
    out += tr(note("item/agentMessage/delta", {**IDS, "itemId": "m1", "delta": "lo"}))
    out += tr(note("item/started", {**IDS, "item": {"type": "agentMessage", "id": "m1", "text": ""}}))
    out += tr(note("item/completed", {**IDS, "item": {"type": "agentMessage", "id": "m1", "text": "Hello"}}))
    assert [(p["type"], p["text"]) for p in out] == [("text.delta", "Hel"), ("text.delta", "lo"), ("text.completed", "Hello")]
    valid(out)


def test_tools():
    tr = Translator()
    cmd = {"type": "commandExecution", "id": "c1", "command": "ls /a", "cwd": "/workspace", "commandActions": [],
           "status": "inProgress"}
    started = tr(note("item/started", {**IDS, "item": cmd}))
    started += tr(note("item/started", {**IDS, "item": {"type": "webSearch", "id": "w1", "query": "weather"}}))
    assert [(p["name"], p["input"], p["server"]) for p in started] == [
        ("shell", {"command": "ls /a", "cwd": "/workspace"}, False), ("web_search", {"query": "weather"}, True)]
    done = tr(note("item/completed", {**IDS, "item": {**cmd, "status": "completed", "exitCode": 2,
                                                      "aggregatedOutput": "y" * 3000}}))
    assert done[0]["tool_use_id"] == "c1" and done[0]["is_error"] is True
    assert done[0]["output"] == "y" * OUTPUT_CHARS and done[0]["truncated"]
    assert tr(note("item/started", {**IDS, "item": {"type": "reasoning", "id": "r1", "summary": [], "content": []}})) == []
    valid(started + done)


def test_usage_rate_limits_and_result():
    tr = Translator()
    usage = {"inputTokens": 10, "cachedInputTokens": 0, "outputTokens": 5, "reasoningOutputTokens": 0, "totalTokens": 15}
    out = tr(note("thread/tokenUsage/updated", {**IDS, "tokenUsage": {"last": usage, "total": usage}}))
    out += tr(note("account/rateLimits/updated", {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 40}}}))
    assert [p["type"] for p in out] == ["model.usage_updated", "model.usage_updated"]      # usage is not a limit
    hit = tr(note("account/rateLimits/updated", {"rateLimits": {"limitId": "codex", "rateLimitReachedType": "rate_limit_reached"}}))
    assert [p["type"] for p in hit] == ["model.usage_updated", "model.rate_limited"]
    valid(out + hit)
    assert tr(note("turn/completed", {"threadId": "th_1", "turn": {**TURN, "status": "completed"}})) == []
    assert tr.result["is_error"] is False and tr.result["usage"]["total"]["total_tokens"] == 15
    tr(note("turn/completed", {"threadId": "th_1", "turn": {**TURN, "status": "failed", "error": {"message": "usage limit"}}}))
    assert tr.result == {**tr.result, "is_error": True, "subtype": "usage limit"}
