"""Protocol B v0 (shared_schemas.agent_service): the tolerant reader callers use, the strict check
agent-service uses, and the checked-in JSON Schema kept current."""

import pytest
from pydantic import ValidationError

from shared_schemas.agent_service import (
    SCHEMA_FILE, ArtifactCreated, ArtifactDeleted, BType, MessageDelta, ToolStarted, TurnCompleted, parse_body,
    schema_text,
)

ENVELOPE = {"v": 0, "session_id": "s", "conversation_id": "c", "turn_id": "t", "seq": 3, "ts": "2026-11-27T00:00:00Z"}


def test_an_event_off_the_stream_is_its_body():
    body = parse_body({**ENVELOPE, "type": "tool.started", "tool_id": "t1", "name": "web_search", "input": {}, "server": True})
    assert isinstance(body, ToolStarted) and body.name == "web_search" and body.server
    done = parse_body({**ENVELOPE, "type": "turn.completed", "status": "completed", "usage": None})
    assert isinstance(done, TurnCompleted) and done.type == BType.TURN_COMPLETED


def test_a_saved_or_removed_file_is_an_artifact_event():
    saved = parse_body({**ENVELOPE, "type": "artifact.created", "location": "private/session-files/s/r.md",
                        "source": "/workspace/work/r.md", "size": 12, "sha256": "ab"}, strict=True)
    assert isinstance(saved, ArtifactCreated) and saved.size == 12
    gone = parse_body({**ENVELOPE, "type": "artifact.deleted", "location": "private/session-files/s/r.md"}, strict=True)
    assert gone == ArtifactDeleted(location="private/session-files/s/r.md")


def test_callers_ignore_what_they_dont_know():
    """Protocol B only adds: a new type is skipped (None), a new field dropped."""
    assert parse_body({**ENVELOPE, "type": "voice.level", "db": -30}) is None
    body = parse_body({**ENVELOPE, "type": "message.delta", "text": "Hi", "lang": "en"})
    assert body == MessageDelta(text="Hi")


def test_strict_rejects_an_unknown_field_or_type():
    with pytest.raises(ValidationError):
        parse_body({**ENVELOPE, "type": "message.delta", "text": "Hi", "lang": "en"}, strict=True)
    with pytest.raises(ValidationError):
        parse_body({**ENVELOPE, "type": "voice.level"}, strict=True)
    assert parse_body({**ENVELOPE, "type": "message.delta", "text": "Hi"}, strict=True) == MessageDelta(text="Hi")


def test_the_checked_in_schema_is_current():
    """Regenerate with `python -m shared_schemas.agent_service`."""
    assert SCHEMA_FILE.read_text() == schema_text()
