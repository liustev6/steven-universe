"""Protocol B v0: what callers receive (control-plane.md "Protocol B v0").

Every event is the envelope plus one body:

    envelope   v, session_id, conversation_id, turn_id, seq, ts, type      (added by the event log)
    body       one model per type, below: what's built here

    session.status      status (ready | ended), reason                       agent-service
    turn.started        (nothing more)                                       A turn.started
    message.delta       text                                                 A text.delta
    message.completed   text                                                 A text.completed
    tool.started        tool_id, name, input, server                         A tool.started
    tool.completed      tool_id, is_error, output, truncated                 A tool.completed
    task.started        task_id, description                                 A task.started
    task.progress       task_id, description, status                         A task.progress
    task.completed      task_id, status, summary                             A task.completed
    turn.completed      status, reason, first_text_ms, duration_ms, usage    A turn.completed
    error               code, message                                        agent-service, A runtime.error,
                                                                             A model.rate_limited

Rules for callers (stable from v0 on):
- Every caller gets every event; a client shows what it needs (C3).
- `seq` only increases, and may skip numbers.
- Additions only: new types and new optional fields. Ignore types you don't know. Anything
  else is a new `v`.

Defined here until CP4 moves it to shared-schemas (other languages, generated clients).
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.db.models import EndReason
from app.eventlog import ENVELOPE, NewEvent


class BType(StrEnum):
    SESSION_STATUS = "session.status"
    TURN_STARTED = "turn.started"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    TURN_COMPLETED = "turn.completed"
    ERROR = "error"


class ErrorCode(StrEnum):
    NO_SANDBOX = "no_sandbox"        # no pod free right now; try again
    RATE_LIMITED = "rate_limited"    # your plan's usage limit was reached
    BUDGET = "budget"                # the profile's limit (turns, tokens per turn, wall time; C13)
    RUNTIME = "runtime"              # the sandbox's runtime reported a problem
    INTERNAL = "internal"            # agent-service's own fault


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStatus(_Body):
    type: Literal[BType.SESSION_STATUS] = BType.SESSION_STATUS
    status: Literal["ready", "ended"]
    reason: EndReason | None = None          # only with `ended`: what to do next depends on it


class TurnStarted(_Body):
    type: Literal[BType.TURN_STARTED] = BType.TURN_STARTED


class MessageDelta(_Body):
    type: Literal[BType.MESSAGE_DELTA] = BType.MESSAGE_DELTA
    text: str


class MessageCompleted(_Body):
    """A text block is finished: its whole text (for speech, and for clients that don't stream)."""
    type: Literal[BType.MESSAGE_COMPLETED] = BType.MESSAGE_COMPLETED
    text: str


class ToolStarted(_Body):
    type: Literal[BType.TOOL_STARTED] = BType.TOOL_STARTED
    tool_id: str
    name: str
    input: dict[str, Any]
    server: bool = False                     # runs at the model provider (e.g. web search)


class ToolCompleted(_Body):
    type: Literal[BType.TOOL_COMPLETED] = BType.TOOL_COMPLETED
    tool_id: str
    is_error: bool
    output: str                              # at most OUTPUT_CHARS
    truncated: bool = False


class TaskStarted(_Body):
    type: Literal[BType.TASK_STARTED] = BType.TASK_STARTED
    task_id: str
    description: str | None = None


class TaskProgress(_Body):
    type: Literal[BType.TASK_PROGRESS] = BType.TASK_PROGRESS
    task_id: str
    description: str | None = None
    status: str | None = None


class TaskCompleted(_Body):
    type: Literal[BType.TASK_COMPLETED] = BType.TASK_COMPLETED
    task_id: str
    status: str
    summary: str | None = None


class Usage(_Body):
    """One turn's tokens (not the session's running total). Names follow OpenAI's usage object."""
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int


class TurnCompleted(_Body):
    """The last event of every turn, whatever happened."""
    type: Literal[BType.TURN_COMPLETED] = BType.TURN_COMPLETED
    status: Literal["completed", "interrupted", "failed"]
    reason: str | None = None
    first_text_ms: int | None = None
    duration_ms: int | None = None
    usage: Usage | None = None               # None when it isn't known (e.g. agent-service restarted mid-turn)


class Error(_Body):
    type: Literal[BType.ERROR] = BType.ERROR
    code: ErrorCode
    message: str


Body = Annotated[
    Union[SessionStatus, TurnStarted, MessageDelta, MessageCompleted, ToolStarted, ToolCompleted,
          TaskStarted, TaskProgress, TaskCompleted, TurnCompleted, Error],
    Field(discriminator="type"),
]
_BODY = TypeAdapter(Body)


def new_event(body: _Body, *, turn_id: uuid.UUID | None = None, source_seq: int | None = None) -> NewEvent:
    """A body, ready for the event log (which adds the envelope)."""
    return NewEvent(body.type, body.model_dump(mode="json", exclude={"type"}), turn_id=turn_id,
                    source_seq=source_seq)


def parse_body(raw: dict[str, Any]) -> Body:
    """A stored event (envelope and all) back into its body model; for tests and clients."""
    return _BODY.validate_python({k: v for k, v in raw.items() if k not in ENVELOPE or k == "type"})


def json_schema() -> dict[str, Any]:
    """Every body's JSON Schema (the envelope is documented above); the basis for CP4's shared-schemas."""
    return _BODY.json_schema()
