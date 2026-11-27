"""Protocol A → B. Events are built with the runtime's own `build_event`: what a sandbox sends."""

import json
import uuid

import pytest

from app.protocol.events import BType, json_schema, parse_body
from app.history.eventlog import EventLog
from app.protocol.translate import Translator
from core.types import OUTPUT_CHARS, build_event

SESSION = uuid.uuid4()
TURN = uuid.uuid4()


class Runtime:
    """Numbers events like the runtime's EventSink."""

    def __init__(self, session=SESSION):
        self.session, self.seq = session, 0

    def __call__(self, payload: dict, turn: uuid.UUID | None = TURN) -> dict:
        e = build_event(payload, session_id=str(self.session), turn_id=str(turn) if turn else None,
                        seq=self.seq, ts=1.0)
        self.seq += 1
        return e.model_dump(mode="json")


def breakdown(total: int, inp: int = 0, cached: int = 0, out: int = 0, reasoning: int = 0) -> dict:
    """Codex's token breakdown, as the runtime sends it (snake_case)."""
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "reasoning_output_tokens": reasoning, "total_tokens": total, "cache_write_input_tokens": 0}


def bodies(translation) -> list[dict]:
    return [{"type": e.type, **e.data} for e in translation.events]


def test_a_whole_turn():
    a, t = Runtime(), Translator(SESSION)
    out = t.translate([
        a({"type": "turn.started"}),
        a({"type": "text.delta", "text": "Hel"}),
        a({"type": "text.delta", "text": "lo"}),
        a({"type": "text.completed", "text": "Hello"}),
        a({"type": "tool.started", "tool_use_id": "c1", "name": "shell", "input": {"cmd": "ls"}}),
        a({"type": "tool.completed", "tool_use_id": "c1", "is_error": False, "output": "a.txt"}),
        a({"type": "text.completed", "text": "There's a.txt."}),
        a({"type": "turn.completed", "status": "completed", "duration_ms": 900, "first_text_ms": 300,
           "stop_reason": "end_turn", "num_steps": 2,
           "usage": {"total": breakdown(150, inp=100, cached=40, out=50), "last": breakdown(50)}}),
    ])
    assert [e["type"] for e in bodies(out)] == [
        "turn.started", "message.delta", "message.delta", "message.completed", "tool.started",
        "tool.completed", "message.completed", "turn.completed"]
    tool = bodies(out)[4]
    assert tool == {"type": "tool.started", "tool_id": "c1", "name": "shell", "input": {"cmd": "ls"}, "server": False}
    done = bodies(out)[-1]
    assert done["usage"] == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 50,
                             "reasoning_tokens": 0, "total_tokens": 150}
    assert "stop_reason" not in done and "num_steps" not in done       # the engine's figures stay internal
    assert all(e.turn_id == TURN for e in out.events)
    assert [e.source_seq for e in out.events] == list(range(8))        # for the log's deduplication
    (result,) = out.turns_done
    assert result.reply_text == "Hello\n\nThere's a.txt." and result.status == "completed"
    assert result.usage["total_tokens"] == 150


def test_usage_is_per_turn_not_the_threads_running_total():
    a, t = Runtime(), Translator(SESSION)
    first, second = uuid.uuid4(), uuid.uuid4()
    t.translate([a({"type": "turn.started"}, first),
                 a({"type": "turn.completed", "status": "completed", "duration_ms": 1,
                    "usage": {"total": breakdown(1000, inp=900, out=100)}}, first)])
    out = t.translate([
        a({"type": "turn.started"}, second),
        a({"type": "model.usage_updated", "info": {"tokens": {"total": breakdown(1200, inp=1050, out=150)}}}, second),
        a({"type": "turn.completed", "status": "completed", "duration_ms": 1,
           "usage": {"total": breakdown(1300, inp=1100, out=200)}}, second)])
    assert out.turns_done[0].usage == {"input_tokens": 200, "cached_input_tokens": 0, "output_tokens": 100,
                                       "reasoning_tokens": 0, "total_tokens": 300}


def test_after_a_restart_usage_is_unknown_until_a_turn_starts_with_a_known_total():
    a, t = Runtime(), Translator(SESSION, fresh=False)
    out = t.translate([a({"type": "turn.started"}),
                       a({"type": "turn.completed", "status": "completed", "duration_ms": 1,
                          "usage": {"total": breakdown(5000)}})])
    assert bodies(out)[-1]["usage"] is None
    nxt = uuid.uuid4()
    out = t.translate([a({"type": "turn.started"}, nxt),
                       a({"type": "turn.completed", "status": "completed", "duration_ms": 1,
                          "usage": {"total": breakdown(5400)}}, nxt)])
    assert bodies(out)[-1]["usage"]["total_tokens"] == 400


def test_an_interrupted_turn_without_figures_has_unknown_usage():
    """Codex reports the thread's total at turn/completed even when interrupted early: the same total
    as at the start. That's "unknown", not "zero tokens" (any model call costs tokens)."""
    a, t = Runtime(), Translator(SESSION)
    first, second = uuid.uuid4(), uuid.uuid4()
    t.translate([a({"type": "turn.started"}, first),
                 a({"type": "turn.completed", "status": "completed", "duration_ms": 1,
                    "usage": {"total": breakdown(900)}}, first)])
    out = t.translate([a({"type": "turn.started"}, second),
                       a({"type": "turn.completed", "status": "interrupted", "duration_ms": 1,
                          "usage": {"total": breakdown(900)}}, second)])
    assert bodies(out)[-1]["usage"] is None and out.turns_done[0].usage is None


def test_signals_are_not_caller_events():
    a, t = Runtime(), Translator(SESSION)
    out = t.translate([
        a({"type": "runtime.ready", "startup_ms": 500, "profile": "voice", "model": None, "tools": []}, None),
        a({"type": "model.usage_updated", "info": {"plan": {"plan_type": "pro", "primary": {"used_percent": 40}}}}, None),
    ])
    assert out.ready and out.events == []


def test_rate_limits_never_carry_the_plans_details():
    a, t = Runtime(), Translator(SESSION)
    out = t.translate([a({"type": "model.rate_limited",
                          "info": {"plan_type": "pro", "rate_limit_reached_type": "primary",
                                   "primary": {"used_percent": 100, "resets_at": 1}}})])
    (e,) = bodies(out)
    assert e == {"type": "error", "code": "rate_limited", "message": "your plan's usage limit was reached"}
    assert "pro" not in json.dumps(e)


def test_the_sandbox_is_checked_not_trusted():
    a, t = Runtime(), Translator(SESSION)
    other = Runtime(uuid.uuid4())
    out = t.translate([
        other({"type": "text.delta", "text": "from another session"}),
        a({"type": "text.delta", "text": "bad turn id"}, None) | {"turn_id": "not-a-uuid"},
        {"type": "text.delta", "text": "no envelope"},
        a({"type": "text.delta", "text": "fine"}),
        "not even a dict",
    ])
    assert [e.data["text"] for e in out.events] == ["fine"] and out.dropped == 4


def test_text_from_the_sandbox_is_capped_again():
    a, t = Runtime(), Translator(SESSION)
    big = "x" * (OUTPUT_CHARS * 3)
    out = t.translate([
        a({"type": "tool.completed", "tool_use_id": "c", "is_error": True, "output": big}),
        a({"type": "runtime.error", "message": big}),
    ])
    tool, err = bodies(out)
    assert len(tool["output"]) == OUTPUT_CHARS and tool["truncated"] is True
    assert err["code"] == "runtime" and len(err["message"]) == 500


def test_tasks_keep_only_bs_fields():
    a, t = Runtime(), Translator(SESSION)
    out = t.translate([
        a({"type": "task.started", "task_id": "k", "description": "search", "task_type": "agent", "tool_use_id": "x"}),
        a({"type": "task.progress", "task_id": "k", "status": "running", "last_tool_name": "shell"}),
        a({"type": "task.completed", "task_id": "k", "status": "completed", "summary": "done"}),
    ])
    assert bodies(out) == [
        {"type": "task.started", "task_id": "k", "description": "search"},
        {"type": "task.progress", "task_id": "k", "description": None, "status": "running"},
        {"type": "task.completed", "task_id": "k", "status": "completed", "summary": "done"},
    ]


async def test_stored_events_round_trip_through_bs_models(db, new_session):
    """What the log stores is valid protocol B: a client's parser accepts every event."""
    session = await new_session()
    a, t = Runtime(session.id), Translator(session.id)
    turn = uuid.uuid4()
    out = t.translate([a({"type": "turn.started"}, turn), a({"type": "text.delta", "text": "hi"}, turn),
                       a({"type": "turn.completed", "status": "interrupted", "duration_ms": 5,
                          "usage": {"total": breakdown(10)}}, turn)])
    stored = await EventLog(db).append(session.id, out.events)
    parsed = [parse_body(e, strict=True) for e in stored]
    assert [p.type for p in parsed] == [BType.TURN_STARTED, BType.MESSAGE_DELTA, BType.TURN_COMPLETED]
    assert parsed[-1].status == "interrupted" and parsed[-1].usage.total_tokens == 10


def test_the_schema_covers_every_type():
    schema = json.dumps(json_schema())
    for t in BType:
        assert f'"{t.value}"' in schema


@pytest.mark.parametrize("bad", [{"type": "message.delta"}, {"type": "nope", "text": "x"}])
def test_parse_body_rejects_bad_events(bad):
    with pytest.raises(ValueError):
        parse_body(bad, strict=True)
