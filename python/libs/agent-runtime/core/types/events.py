"""Runtime -> gateway: events (POST /v1/runtime/events), one Pydantic model per type.

Every event carries the envelope (v, session_id, turn_id, seq, ts) and a `type` from EventType.
Senders build events through `build_event`; receivers parse each with `parse_event` and drop a bad
one, never the whole batch (types/__init__.py has the rules).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1
OUTPUT_CHARS = 2000                # tool output kept in tool.completed


class EventType(StrEnum):
    RUNTIME_READY = "runtime.ready"
    RUNTIME_ERROR = "runtime.error"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    TEXT_DELTA = "text.delta"
    TEXT_COMPLETED = "text.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    MODEL_USAGE_UPDATED = "model.usage_updated"
    MODEL_RATE_LIMITED = "model.rate_limited"


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class _Event(BaseModel):
    """The envelope every event carries."""

    model_config = ConfigDict(extra="forbid")
    v: Literal[1] = PROTOCOL_VERSION
    session_id: str
    turn_id: str | None = None     # None for session-level events (runtime.ready, some errors)
    seq: int                       # per session, increasing: reconnects resume after a seq
    ts: float                      # unix time, seconds


class RuntimeReady(_Event):
    """Once per session: the SDK has started with the profile."""
    type: Literal[EventType.RUNTIME_READY] = EventType.RUNTIME_READY
    startup_ms: int                # from the session file appearing (the claim) to ready
    profile: str
    model: str | None              # None: Codex's default model
    tools: list[str]


class RuntimeErrorEvent(_Event):
    """Something the runtime itself hit (bad message, busy, crash); a turn also ends with turn.completed."""
    type: Literal[EventType.RUNTIME_ERROR] = EventType.RUNTIME_ERROR
    message: str


class TurnStarted(_Event):
    type: Literal[EventType.TURN_STARTED] = EventType.TURN_STARTED


class TurnCompleted(_Event):
    """The last event of every turn, whatever happened."""
    type: Literal[EventType.TURN_COMPLETED] = EventType.TURN_COMPLETED
    status: TurnStatus
    reason: str | None = None              # e.g. "busy", "runtime error", the SDK's error subtype
    first_text_ms: int | None = None       # turn start -> first text.delta
    duration_ms: int                       # turn start -> this event
    stop_reason: str | None = None         # the model's stop reason, if the engine reports one
    num_steps: int | None = None           # steps used in this turn, if the engine reports it
    usage: dict[str, Any] | None = None    # token usage as the engine reports it
    total_cost_usd: float | None = None    # API-key billing only; empty on a subscription


class TextDelta(_Event):
    """Assistant text, streamed as generated (top-level agent only; subagents stay inside their task)."""
    type: Literal[EventType.TEXT_DELTA] = EventType.TEXT_DELTA
    text: str


class TextCompleted(_Event):
    """A text block is finished: its full text. The cue for speech to flush, and for non-streaming clients."""
    type: Literal[EventType.TEXT_COMPLETED] = EventType.TEXT_COMPLETED
    text: str


class ToolStarted(_Event):
    """The agent called a tool. Full input: the trusted gateway decides what clients may see."""
    type: Literal[EventType.TOOL_STARTED] = EventType.TOOL_STARTED
    tool_use_id: str
    name: str
    input: dict[str, Any]
    server: bool = False           # a server-side tool (runs at the model provider, e.g. web search)


class ToolCompleted(_Event):
    type: Literal[EventType.TOOL_COMPLETED] = EventType.TOOL_COMPLETED
    tool_use_id: str
    is_error: bool
    output: str                    # text of the result, capped at OUTPUT_CHARS
    truncated: bool = False


class TaskStarted(_Event):
    """A background task or subagent started (the SDK's Task* messages)."""
    type: Literal[EventType.TASK_STARTED] = EventType.TASK_STARTED
    task_id: str
    description: str | None = None
    task_type: str | None = None
    tool_use_id: str | None = None


class TaskProgress(_Event):
    type: Literal[EventType.TASK_PROGRESS] = EventType.TASK_PROGRESS
    task_id: str
    description: str | None = None
    status: str | None = None
    last_tool_name: str | None = None


class TaskCompleted(_Event):
    type: Literal[EventType.TASK_COMPLETED] = EventType.TASK_COMPLETED
    task_id: str
    status: str                    # the SDK's terminal status (e.g. completed, failed, stopped)
    summary: str | None = None


class ModelUsageUpdated(_Event):
    """A usage snapshot (tokens so far, the plan's usage): information, not a limit."""
    type: Literal[EventType.MODEL_USAGE_UPDATED] = EventType.MODEL_USAGE_UPDATED
    info: dict[str, Any]


class ModelRateLimited(_Event):
    """A limit was actually hit: the plan's usage limit, or the gateway's budget."""
    type: Literal[EventType.MODEL_RATE_LIMITED] = EventType.MODEL_RATE_LIMITED
    info: dict[str, Any]


AgentEvent = Annotated[
    Union[RuntimeReady, RuntimeErrorEvent, TurnStarted, TurnCompleted, TextDelta, TextCompleted,
          ToolStarted, ToolCompleted, TaskStarted, TaskProgress, TaskCompleted, ModelUsageUpdated, ModelRateLimited],
    Field(discriminator="type"),
]
_EVENT = TypeAdapter(AgentEvent)


def build_event(payload: dict[str, Any], *, session_id: str, turn_id: str | None, seq: int, ts: float) -> AgentEvent:
    """Sender side: envelope + payload, validated. Raises on a malformed event (a bug)."""
    return _EVENT.validate_python({**payload, "session_id": session_id, "turn_id": turn_id, "seq": seq, "ts": ts})


def parse_event(raw: dict[str, Any]) -> AgentEvent:
    """Receiver side: one event; raises pydantic.ValidationError if unknown or malformed."""
    return _EVENT.validate_python(raw)


def event_json_schema() -> dict[str, Any]:
    """The JSON Schema of every event (documentation; the basis for other languages if ever needed)."""
    return _EVENT.json_schema()


class EventBatch(BaseModel):
    """On the wire: plain dicts, so one bad event can't reject the batch (see parse_event)."""
    events: list[dict[str, Any]] = Field(default_factory=list)
