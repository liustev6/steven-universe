"""Protocol A (from the sandbox) → protocol B (to callers), for one session.

The sandbox is assumed compromised (G14), so its events are checked, not trusted:
- each is parsed on its own (protocol A's models); an unknown or malformed one is dropped, never
  the batch;
- its `session_id` must be this session's, and its `turn_id` a turn agent-service started;
- text it reports is capped again (tool output, error messages): a runtime's caps are a courtesy.

What becomes of each protocol A event:

    runtime.ready          no B event: a signal (`ready`); the sessions core sends session.status
    runtime.error          error {code: runtime, message}
    turn.started           turn.started
    text.delta             message.delta
    text.completed         message.completed (and the turn's reply text, for `turns`)
    tool.started/completed tool.started/completed (tool_use_id → tool_id)
    task.*                 task.* (only the fields B defines)
    model.usage_updated    no B event: token totals, for this turn's usage
    model.rate_limited     error {code: rate_limited}; never the plan's details
    turn.completed         turn.completed, with this turn's own tokens; and `turn_done`, for `turns`

Codex reports a thread's *cumulative* tokens, so a turn's usage is the total at its end minus the
total at its start. It's what the sandbox *says*: for callers to see, never for budgets. The
gateway counts budgets from the model responses it relays itself (C13), which a sandbox can't forge. A translator made for a session that may already have used tokens (agent-service
restarted mid-session) starts with the total unknown, and reports `usage: null` until it knows.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.protocol import events as b
from app.history.eventlog import NewEvent
from core.types import OUTPUT_CHARS, EventType, parse_event

logger = logging.getLogger(__name__)

MESSAGE_CHARS = 500                      # an error message from the sandbox, at most
TOKEN_FIELDS = {                         # B's name: Codex's name (in its token usage breakdown)
    "input_tokens": "input_tokens",
    "cached_input_tokens": "cached_input_tokens",
    "output_tokens": "output_tokens",
    "reasoning_tokens": "reasoning_output_tokens",
    "total_tokens": "total_tokens",
}
ZERO = {k: 0 for k in TOKEN_FIELDS}


@dataclass
class TurnResult:
    """What the `turns` row records when a turn ends."""
    turn_id: uuid.UUID
    status: str
    reason: str | None
    reply_text: str
    first_text_ms: int | None
    duration_ms: int | None
    usage: dict[str, int] | None


@dataclass
class Translation:
    events: list[NewEvent] = field(default_factory=list)   # for the event log, in order
    ready: bool = False                                      # runtime.ready arrived
    turns_done: list[TurnResult] = field(default_factory=list)
    dropped: int = 0                                         # events refused (logged)


class Translator:
    def __init__(self, session_id: uuid.UUID, *, fresh: bool = True):
        self.session_id = session_id
        self._total: dict[str, int] | None = dict(ZERO) if fresh else None  # the thread's tokens so far
        self._turn_start: dict[str, int] | None = None                       # ... when the turn began
        self._replies: dict[uuid.UUID, list[str]] = {}

    def translate(self, raw_events: list[dict[str, Any]]) -> Translation:
        out = Translation()
        for raw in raw_events:
            try:
                a = parse_event(raw)
                turn_id = self._check(a)
            except (ValidationError, ValueError) as exc:
                out.dropped += 1
                logger.warning("session %s: dropped a protocol A event (type %r): %s", self.session_id,
                               raw.get("type") if isinstance(raw, dict) else None, str(exc)[:200])
                continue
            body = self._one(a, turn_id, out)
            if body is not None:
                out.events.append(b.new_event(body, turn_id=turn_id, source_seq=a.seq))
        return out

    # ---- checks ----------------------------------------------------------------------------------
    def _check(self, a) -> uuid.UUID | None:
        if a.session_id != str(self.session_id):
            raise ValueError(f"session_id {a.session_id!r} isn't this session's")
        return uuid.UUID(a.turn_id) if a.turn_id is not None else None   # ValueError if not a UUID

    # ---- one event -------------------------------------------------------------------------------
    def _one(self, a, turn_id: uuid.UUID | None, out: Translation) -> b._Body | None:
        t = a.type
        if t == EventType.RUNTIME_READY:
            out.ready = True
            return None
        if t == EventType.RUNTIME_ERROR:
            return b.Error(code=b.ErrorCode.RUNTIME, message=a.message[:MESSAGE_CHARS])
        if t == EventType.TURN_STARTED:
            self._turn_start = dict(self._total) if self._total is not None else None
            if turn_id is not None:
                self._replies[turn_id] = []
            return b.TurnStarted()
        if t == EventType.TEXT_DELTA:
            return b.MessageDelta(text=a.text)
        if t == EventType.TEXT_COMPLETED:
            if turn_id is not None:
                self._replies.setdefault(turn_id, []).append(a.text)
            return b.MessageCompleted(text=a.text)
        if t == EventType.TOOL_STARTED:
            return b.ToolStarted(tool_id=a.tool_use_id, name=a.name, input=a.input, server=a.server)
        if t == EventType.TOOL_COMPLETED:
            return b.ToolCompleted(tool_id=a.tool_use_id, is_error=a.is_error, output=a.output[:OUTPUT_CHARS],
                                   truncated=a.truncated or len(a.output) > OUTPUT_CHARS)
        if t == EventType.TASK_STARTED:
            return b.TaskStarted(task_id=a.task_id, description=a.description)
        if t == EventType.TASK_PROGRESS:
            return b.TaskProgress(task_id=a.task_id, description=a.description, status=a.status)
        if t == EventType.TASK_COMPLETED:
            return b.TaskCompleted(task_id=a.task_id, status=a.status, summary=a.summary)
        if t == EventType.MODEL_USAGE_UPDATED:
            total = _tokens((a.info.get("tokens") or {}).get("total"))
            if total is not None:
                self._total = total
            return None
        if t == EventType.MODEL_RATE_LIMITED:
            return b.Error(code=b.ErrorCode.RATE_LIMITED, message="your plan's usage limit was reached")
        if t == EventType.TURN_COMPLETED:
            total = _tokens(((a.usage or {}).get("total")))
            if total is not None:
                self._total = total
            usage = None
            if self._total is not None and self._turn_start is not None:
                usage = {k: self._total[k] - self._turn_start[k] for k in TOKEN_FIELDS}
            self._turn_start = None
            if turn_id is not None:
                out.turns_done.append(TurnResult(
                    turn_id=turn_id, status=a.status.value, reason=a.reason,
                    reply_text="\n\n".join(self._replies.pop(turn_id, [])),
                    first_text_ms=a.first_text_ms, duration_ms=a.duration_ms, usage=usage))
            return b.TurnCompleted(status=a.status.value, reason=a.reason, first_text_ms=a.first_text_ms,
                                   duration_ms=a.duration_ms, usage=b.Usage(**usage) if usage else None)
        return None                                  # a type protocol A added later: nothing for callers yet


def _tokens(breakdown: Any) -> dict[str, int] | None:
    """Codex's token breakdown (snake_case, from the runtime) in B's names; None if it isn't one."""
    if not isinstance(breakdown, dict) or "total_tokens" not in breakdown:
        return None
    try:
        return {ours: int(breakdown.get(theirs, 0)) for ours, theirs in TOKEN_FIELDS.items()}
    except (TypeError, ValueError):
        return None
