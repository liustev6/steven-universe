"""Protocol A: the contract between a sandbox's runtime and its gateway (agent-runtime.md).

Internal: only agent-runtime and agent-service speak it, and they deploy together. Clients never
see it: agent-service translates it into protocol B (shared-schemas). So it may stay close to the
Claude Agent SDK, but never exposes SDK classes directly.

    session.py   the session file, written at claim, read once     SessionFile (Profile, Limits)
    inbound.py   gateway -> runtime: the long poll's commands       TurnMessage | InterruptMessage | EndMessage
    events.py    runtime -> gateway: events, one model per type     AgentEvent, EventBatch

Rules:
- Every event has the envelope (v, session_id, turn_id, seq, ts) and a `type` from EventType.
- Senders build events through the models (`build_event`), so a malformed event fails at once.
- Receivers parse events one by one (`parse_event`): an unknown or malformed event is dropped and
  logged, never the whole batch (a newer runtime may briefly talk to an older gateway).
- Changes are additive: new event types and optional fields; bump PROTOCOL_VERSION otherwise.
"""

from core.types.events import (
    OUTPUT_CHARS, PROTOCOL_VERSION, AgentEvent, EventBatch, EventType, ModelRateLimited, ModelUsageUpdated,
    RuntimeErrorEvent,
    RuntimeReady, TaskCompleted, TaskProgress, TaskStarted, TextCompleted, TextDelta, ToolCompleted, ToolStarted,
    TurnCompleted, TurnStarted, TurnStatus, build_event, event_json_schema, parse_event,
)
from core.types.inbound import EndMessage, InboundBatch, InboundMessage, InterruptMessage, TurnMessage
from core.types.session import SESSION_FILE, Limits, Profile, SessionFile, Tool

__all__ = [
    # session file
    "SESSION_FILE", "Limits", "Profile", "SessionFile", "Tool",
    # gateway -> runtime
    "EndMessage", "InboundBatch", "InboundMessage", "InterruptMessage", "TurnMessage",
    # runtime -> gateway
    "OUTPUT_CHARS", "PROTOCOL_VERSION", "AgentEvent", "EventBatch", "EventType", "TurnStatus",
    "RuntimeReady", "RuntimeErrorEvent", "TurnStarted", "TurnCompleted", "TextDelta", "TextCompleted",
    "ToolStarted", "ToolCompleted", "TaskStarted", "TaskProgress", "TaskCompleted", "ModelUsageUpdated",
    "ModelRateLimited",
    "build_event", "parse_event", "event_json_schema",
]
