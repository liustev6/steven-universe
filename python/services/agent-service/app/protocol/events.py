"""Protocol B v0, what callers receive: defined in shared-schemas (`shared_schemas.agent_service`, with
its rules for callers), shared with them; re-exported here, with what only agent-service does:
`new_event`, a body ready for the event log."""

from __future__ import annotations

import uuid

from shared_schemas.agent_service import (  # noqa: F401  (the protocol, for agent-service's modules)
    ENVELOPE_FIELDS, VERSION, ArtifactCreated, ArtifactDeleted, Body, BType, EndReason, Error, ErrorCode, MessageCompleted, MessageDelta,
    SessionStatus, TaskCompleted, TaskProgress, TaskStarted, ToolCompleted, ToolStarted, TurnCompleted,
    TurnStarted, Usage, _Body, json_schema, parse_body,
)

from app.history.eventlog import NewEvent


def new_event(body: _Body, *, turn_id: uuid.UUID | None = None, source_seq: int | None = None) -> NewEvent:
    """A body, ready for the event log (which adds the envelope)."""
    return NewEvent(body.type, body.model_dump(mode="json", exclude={"type"}), turn_id=turn_id,
                    source_seq=source_seq)
