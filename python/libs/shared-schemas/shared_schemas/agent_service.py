"""agent-service's protocol B v0: what its callers receive (docs/specs/agent-service/control-plane.md
"Protocol B v0"). agent-service builds these events; its callers (pi-server, the `agent` CLI,
api-service's relay) read them with `parse_body`. `schemas/agent_service.protocol_b.schema.json` is
generated from here (`python -m shared_schemas.agent_service`), for clients in other languages.

Every event is the envelope plus one body:

    envelope   v, session_id, conversation_id, turn_id, seq, ts, type      (added by agent-service's event log)
    body       one model per type, below

    session.status      status (ready | ended), reason
    turn.started        (nothing more)
    message.delta       text
    message.completed   text
    tool.started        tool_id, name, input, server
    tool.completed      tool_id, is_error, output, truncated
    task.started        task_id, description
    task.progress       task_id, description, status
    task.completed      task_id, status, summary
    turn.completed      status, reason, first_text_ms, duration_ms, usage
    artifact.created    location, source, size, sha256     (a file saved by `sfsync put`)
    artifact.deleted    location                           (a file removed by `sfsync rm`)
    error               code, message

Rules for callers (stable from v0 on):
- Every caller gets every event; a client shows what it needs (C3).
- `seq` only increases, and may skip numbers.
- Additions only: new types and new optional fields. **Ignore types and fields you don't know**
  (`parse_body` does). Anything else is a new `v`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

VERSION = 0                                  # the envelope's `v`

# The envelope's fields: on every event, beside its body's (a body never sets them).
ENVELOPE_FIELDS = frozenset({"v", "session_id", "conversation_id", "turn_id", "seq", "ts", "type"})


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
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_DELETED = "artifact.deleted"
    ERROR = "error"


class EndReason(StrEnum):
    """Why a session ended (`session.status` ended): what to do next depends on it."""
    DELETED = "deleted"            # the caller ended it
    IDLE = "idle"                  # no turn for 5 minutes (C6)
    WALL_TIME = "wall_time"        # the profile's wall time
    SANDBOX_LOST = "sandbox_lost"  # its pod died; a new session in the conversation continues (C6)
    STOPPED = "stopped"            # the kill switch (C14)
    FAILED = "failed"              # it never became ready (no sandbox, a runtime error at start)


class ErrorCode(StrEnum):
    NO_SANDBOX = "no_sandbox"        # no pod free right now; try again
    RATE_LIMITED = "rate_limited"    # your plan's usage limit was reached
    BUDGET = "budget"                # the profile's limit (turns, tokens per turn, wall time; C13)
    RUNTIME = "runtime"              # the sandbox's runtime reported a problem
    INTERNAL = "internal"            # agent-service's own fault


class _Body(BaseModel):
    # Strict when built or checked (agent-service); callers read with `parse_body`, which drops fields
    # it doesn't know first (the tolerant reader the rules ask for).
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
    output: str                              # at most agent-service's OUTPUT_CHARS
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


class ArtifactCreated(_Body):
    """The session saved a file to storage (`sfsync put`; files-and-mounts.md F7): created or replaced."""
    type: Literal[BType.ARTIFACT_CREATED] = BType.ARTIFACT_CREATED
    location: str                            # `<bucket>/<key>`, as file-management names it
    source: str | None = None                # the file in the sandbox it came from, as the agent named it
    size: int                                # bytes
    sha256: str                              # hex, of what was saved


class ArtifactDeleted(_Body):
    """The session removed a file from storage (`sfsync rm`)."""
    type: Literal[BType.ARTIFACT_DELETED] = BType.ARTIFACT_DELETED
    location: str


class Error(_Body):
    type: Literal[BType.ERROR] = BType.ERROR
    code: ErrorCode
    message: str


Body = Annotated[
    Union[SessionStatus, TurnStarted, MessageDelta, MessageCompleted, ToolStarted, ToolCompleted,
          TaskStarted, TaskProgress, TaskCompleted, TurnCompleted, ArtifactCreated, ArtifactDeleted, Error],
    Field(discriminator="type"),
]
_BODY: TypeAdapter[Body] = TypeAdapter(Body)
_MODELS: dict[str, type[_Body]] = {
    m.model_fields["type"].default.value: m
    for m in (SessionStatus, TurnStarted, MessageDelta, MessageCompleted, ToolStarted, ToolCompleted,
              TaskStarted, TaskProgress, TaskCompleted, TurnCompleted, ArtifactCreated, ArtifactDeleted, Error)
}


def parse_body(raw: dict[str, Any], *, strict: bool = False) -> Body | None:
    """An event (envelope and all, as it comes off the stream or the history) as its body model.

    The default is the tolerant reader callers need: a type it doesn't know is None, and fields it
    doesn't know are dropped (protocol B only ever adds). `strict=True` (agent-service checking what
    it wrote): any unknown type or field raises.
    """
    if strict:
        return _BODY.validate_python({k: v for k, v in raw.items() if k not in ENVELOPE_FIELDS or k == "type"})
    model = _MODELS.get(raw.get("type"))  # type: ignore[arg-type]
    if model is None:
        return None
    return model.model_validate({k: v for k, v in raw.items() if k in model.model_fields})


def json_schema() -> dict[str, Any]:
    """Every body's JSON Schema (the envelope is described above)."""
    return _BODY.json_schema()


SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schemas" / "agent_service.protocol_b.schema.json"


def schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":                   # regenerate the checked-in schema
    SCHEMA_FILE.parent.mkdir(exist_ok=True)
    SCHEMA_FILE.write_text(schema_text())
    print(f"wrote {SCHEMA_FILE}")
