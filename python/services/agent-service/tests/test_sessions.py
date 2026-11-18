"""The sessions core (C5–C7, C9, C14) on a real PostgreSQL, with a fake runner."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.config import Settings
from app.db.models import ChatSession, Conversation, EndReason, SessionState, Turn, TurnStatus
from app.history.eventlog import EventLog
from app.history.carry import HEADER
from app.sessions.runner import NoSandbox
from app.sessions import (
    Busy, BudgetExceeded, Ended, InstructionsNotAllowed, NotFound, ProfileMismatch, Sessions, Stopped, UnknownProfile,
    capability_hash,
)
from tests.helpers import ME, OTHER, PROFILES, FakeRunner, Runtime

@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def make(db, runner):
    def make_sessions() -> Sessions:            # a new one = agent-service restarted
        return Sessions(db, EventLog(db), runner, PROFILES, Settings())
    return make_sessions


async def ready_session(sessions, profile="chat", conversation=None):
    row, created = await sessions.start(ME, profile, conversation)
    rt = Runtime(row.id)
    await sessions.ingest(row.id, [rt.ready()])
    return row, rt


async def state(db, session_id):
    async with db() as s:
        return await s.get(ChatSession, session_id)


# ---- start ----------------------------------------------------------------------------------------
async def test_start_claims_a_pod_with_the_profile(make, runner, db):
    sessions = make()
    row, created = await sessions.start(ME, "voice")
    assert created and row.state == SessionState.STARTING and row.owner == ME
    (claim,) = runner.claims
    assert claim["template"] == "chat" and claim["session_id"] == row.id
    assert claim["profile"]["name"] == "voice" and claim["profile"]["effort"] == "low"
    assert "template" not in claim["profile"]                       # agent-service's own settings stay here
    stored = await state(db, row.id)
    assert stored.sandbox_id == claim["sandbox_id"]
    assert stored.capability_hash == capability_hash(claim["capability"])   # only the hash is stored (C7)
    assert len(claim["capability"]) >= 43                           # 256 bits, base64url


async def test_a_conversations_live_session_is_reused(make, runner):
    sessions = make()
    first, _ = await sessions.start(ME, "voice")
    again, created = await sessions.start(ME, "voice", first.conversation_id)
    assert not created and again.id == first.id and len(runner.claims) == 1


async def test_racing_starts_claim_one_pod(make, runner):
    sessions = make()
    first, _ = await sessions.start(ME, "voice")
    await sessions.end(first.id, EndReason.DELETED)
    runner.claim_delay = 0.05
    results = await asyncio.gather(*(sessions.start(ME, "voice", first.conversation_id) for _ in range(5)))
    assert len({r.id for r, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert len(runner.claims) == 2                                  # the first session's, and one more


async def test_ownership_and_profile_rules(make):
    sessions = make()
    row, _ = await sessions.start(ME, "voice")
    with pytest.raises(NotFound):
        await sessions.start(OTHER, "voice", row.conversation_id)  # someone else's: 404, not 403 (C2)
    with pytest.raises(NotFound):
        await sessions.get(row.id, OTHER)
    with pytest.raises(ProfileMismatch):
        await sessions.start(ME, "chat", row.conversation_id)
    with pytest.raises(UnknownProfile):
        await sessions.start(ME, "nope")
    assert (await sessions.get(row.id, None)).id == row.id          # chat:admin sees every session


async def test_no_sandbox_leaves_nothing_behind(make, runner, db):
    sessions = make()
    runner.no_sandbox = True
    with pytest.raises(NoSandbox) as exc:
        await sessions.start(ME, "voice")
    assert exc.value.retry_after == 7
    async with db() as s:
        assert (await s.scalars(select(Conversation))).all() == []
        assert (await s.scalars(select(ChatSession))).all() == []


# ---- turns ----------------------------------------------------------------------------------------
async def test_a_whole_turn(make, db):
    sessions = make()
    row, rt = await ready_session(sessions)
    assert (await state(db, row.id)).state == SessionState.READY
    turn, after = await sessions.turn(row.id, ME, "hi")
    assert (await state(db, row.id)).state == SessionState.BUSY
    (msg,) = await sessions.next_messages(row.id, 1)
    assert msg == {"type": "turn", "turn_id": str(turn.id), "text": "hi"}
    await sessions.ingest(row.id, rt.answer(turn.id))
    s = await state(db, row.id)
    assert s.state == SessionState.READY and s.turns_used == 1
    async with db() as d:
        t = await d.get(Turn, turn.id)
    assert t.status == TurnStatus.COMPLETED and t.reply_text == "Hello" and t.delivered_at is not None
    assert t.usage["total_tokens"] == 100
    events = await sessions.log.read(row.id, after)
    assert [e["type"] for e in events] == ["turn.started", "message.delta", "message.completed", "turn.completed"]
    assert [e["type"] for e in await sessions.log.read(row.id)][0] == "session.status"   # ready came first


async def test_a_turn_can_wait_for_a_starting_session(make, db):
    sessions = make()
    row, _ = await sessions.start(ME, "voice")
    turn, _ = await sessions.turn(row.id, ME, "early")
    assert (await state(db, row.id)).state == SessionState.BUSY
    await sessions.ingest(row.id, [Runtime(row.id).ready()])
    s = await state(db, row.id)
    assert s.state == SessionState.BUSY and s.ready_at is not None   # still busy: the turn is queued
    assert [m["text"] for m in await sessions.next_messages(row.id, 1)] == ["early"]


async def test_one_turn_at_a_time_and_max_turns(make):
    sessions = make()
    row, rt = await ready_session(sessions, "smoke")                 # max_turns: 2
    turn, _ = await sessions.turn(row.id, ME, "1")
    with pytest.raises(Busy):
        await sessions.turn(row.id, ME, "again")
    await sessions.ingest(row.id, rt.answer(turn.id))
    turn, _ = await sessions.turn(row.id, ME, "2")
    await sessions.ingest(row.id, rt.answer(turn.id, total=200))
    with pytest.raises(BudgetExceeded):
        await sessions.turn(row.id, ME, "3")


async def test_cancel_sends_an_interrupt(make):
    sessions = make()
    row, _ = await ready_session(sessions)
    assert not await sessions.cancel(row.id, ME)                     # nothing running
    turn, _ = await sessions.turn(row.id, ME, "long one")
    await sessions.next_messages(row.id, 1)
    assert await sessions.cancel(row.id, ME)
    assert await sessions.next_messages(row.id, 1) == [{"type": "interrupt", "turn_id": str(turn.id)}]


async def test_the_runtimes_poll_waits_then_returns_nothing(make):
    sessions = make()
    row, _ = await ready_session(sessions)
    assert await sessions.next_messages(row.id, 0.05) == []


# ---- the carry-over (C9) --------------------------------------------------------------------------
async def test_a_new_session_carries_the_last_turns(make, runner):
    sessions = make()
    row, rt = await ready_session(sessions, "voice")                 # carry_turns: 4
    for i in range(6):
        turn, _ = await sessions.turn(row.id, ME, f"question {i}")
        await sessions.ingest(row.id, rt.answer(turn.id, text=f"answer {i}", total=100 * (i + 1)))
    await sessions.end(row.id, EndReason.IDLE)
    second, created = await sessions.start(ME, "voice", row.conversation_id)
    assert created and second.id != row.id
    instructions = runner.claims[-1]["profile"]["instructions"]
    assert instructions.startswith("You answer by voice.") and HEADER in instructions
    assert "question 1" not in instructions and "User: question 2\nAssistant: answer 2" in instructions
    assert instructions.rstrip().endswith("Assistant: answer 5")


# ---- the caller's instructions (C15) ------------------------------------------------------------------
HOME = "The panel is in Steven's home in Seattle; time zone America/Los_Angeles."


async def test_the_callers_instructions_follow_the_profiles_and_are_kept(make, runner, db):
    sessions = make()
    row, _ = await sessions.start(ME, "panel", instructions=f"  {HOME}\n")
    instructions = runner.claims[-1]["profile"]["instructions"]
    assert instructions == f"You are the panel's voice assistant.\n\n{HOME}"     # the profile's first
    assert (await state(db, row.id)).instructions == HOME                       # what it was told, stored


async def test_the_callers_instructions_come_before_the_carried_turns(make, runner):
    sessions = make()
    row, _ = await sessions.start(ME, "panel", instructions=HOME)
    rt = Runtime(row.id)
    await sessions.ingest(row.id, [rt.ready()])
    turn, _ = await sessions.turn(row.id, ME, "what's the weather")
    await sessions.ingest(row.id, rt.answer(turn.id, text="Rain.", total=100))
    await sessions.end(row.id, EndReason.IDLE)
    await sessions.start(ME, "panel", row.conversation_id, instructions=HOME)
    instructions = runner.claims[-1]["profile"]["instructions"]
    assert instructions.index("You are the panel's") < instructions.index(HOME) < instructions.index(HEADER)
    assert instructions.rstrip().endswith("Assistant: Rain.")


async def test_a_profile_without_caller_instructions_refuses_them(make, runner, db):
    sessions = make()
    with pytest.raises(InstructionsNotAllowed):
        await sessions.start(ME, "voice", instructions=HOME)
    assert runner.claims == []                                                  # nothing claimed
    async with db() as s:
        assert (await s.execute(select(Conversation))).scalars().all() == []    # nor created
    row, _ = await sessions.start(ME, "voice", instructions="   ")              # blank: none given
    assert (await state(db, row.id)).instructions is None


async def test_a_new_conversation_carries_nothing(make, runner):
    await make().start(ME, "voice")
    assert HEADER not in runner.claims[0]["profile"]["instructions"]


# ---- ending ---------------------------------------------------------------------------------------
async def test_end_releases_announces_and_kills_the_capability(make, runner, db):
    sessions = make()
    row, _ = await ready_session(sessions)
    capability = runner.claims[0]["capability"]
    assert sessions.live_for(capability).session_id == row.id
    turn, _ = await sessions.turn(row.id, ME, "unfinished")
    await sessions.next_messages(row.id, 1)
    assert await sessions.end(row.id, EndReason.DELETED, ME)
    assert not await sessions.end(row.id, EndReason.DELETED, ME)    # once
    s = await state(db, row.id)
    assert s.state == SessionState.ENDED and s.end_reason == EndReason.DELETED and s.capability_hash is None
    assert sessions.live_for(capability) is None                     # dead with the session (C7)
    assert runner.released == [runner.claims[0]["sandbox_id"]]
    async with db() as d:
        assert (await d.get(Turn, turn.id)).status == TurnStatus.FAILED
    last = (await sessions.log.read(row.id))[-1]
    assert last["type"] == "session.status" and last["status"] == "ended" and last["reason"] == "deleted"
    assert await sessions.next_messages(row.id, 1) == [{"type": "end"}]
    with pytest.raises(Ended):
        await sessions.turn(row.id, ME, "too late")


async def test_others_cannot_end_my_session(make):
    sessions = make()
    row, _ = await ready_session(sessions)
    with pytest.raises(NotFound):
        await sessions.end(row.id, EndReason.DELETED, OTHER)


async def test_events_after_the_end_are_dropped(make):
    sessions = make()
    row, rt = await ready_session(sessions)
    await sessions.end(row.id, EndReason.DELETED)
    assert await sessions.ingest(row.id, [rt({"type": "text.delta", "text": "late"})]) == 1


# ---- the sweep: timers and lost sandboxes ---------------------------------------------------------
async def age(db, session_id, **columns_ago):
    now = datetime.now(UTC)
    async with db.begin() as s:
        await s.execute(update(ChatSession).where(ChatSession.id == session_id)
                        .values(**{c: now - timedelta(seconds=secs) for c, secs in columns_ago.items()}))


@pytest.mark.parametrize("columns, reason", [
    ({"last_activity_at": 301}, EndReason.IDLE),
    ({"started_at": 3601, "last_activity_at": 0}, EndReason.WALL_TIME),   # chat's wall time: 3600 s
])
async def test_timers(make, db, columns, reason):
    sessions = make()
    row, _ = await ready_session(sessions)
    await age(db, row.id, **columns)
    await sessions.sweep()
    assert (await state(db, row.id)).end_reason == reason


async def test_a_busy_session_is_never_idle(make, db):
    sessions = make()
    row, _ = await ready_session(sessions)
    await sessions.turn(row.id, ME, "a long one")
    await age(db, row.id, last_activity_at=3000)
    await sessions.sweep()
    assert (await state(db, row.id)).state == SessionState.BUSY


async def test_a_runtime_that_never_gets_ready_fails(make, db):
    sessions = make()
    row, _ = await sessions.start(ME, "voice")
    await sessions.turn(row.id, ME, "queued")                        # busy, but never ready
    await age(db, row.id, started_at=61)
    await sessions.sweep()
    assert (await state(db, row.id)).end_reason == EndReason.FAILED


async def test_a_lost_sandbox_ends_the_session(make, runner, db):
    sessions = make()
    row, _ = await ready_session(sessions)
    runner.unreachable = True
    runner.pods.clear()                                              # the runner restarted: its pods are gone
    await sessions.sweep()
    assert (await state(db, row.id)).state != SessionState.ENDED     # can't tell while it's unreachable
    runner.unreachable = False
    await sessions.sweep()
    assert (await state(db, row.id)).end_reason == EndReason.SANDBOX_LOST


# ---- a restart (C5) -------------------------------------------------------------------------------
async def test_a_restart_keeps_live_sessions(make, db, runner):
    before = make()
    row, rt = await ready_session(before)
    turn, _ = await before.turn(row.id, ME, "never picked up")
    after = make()                                                   # memory gone
    await after.load()
    assert after.live_for(runner.claims[0]["capability"]).session_id == row.id   # rebuilt from the hash
    assert await after.next_messages(row.id, 1) == [{"type": "turn", "turn_id": str(turn.id), "text": "never picked up"}]
    await after.ingest(row.id, rt.answer(turn.id))                   # the runtime carries on
    async with db() as d:
        t = await d.get(Turn, turn.id)
    assert t.status == TurnStatus.COMPLETED and t.usage is None      # usage: unknown after a restart
    seqs = [e["seq"] for e in await after.log.read(row.id)]
    assert seqs == list(range(len(seqs)))                            # numbering carried on


async def test_a_delivered_turn_is_not_sent_twice_after_a_restart(make):
    before = make()
    row, _ = await ready_session(before)
    await before.turn(row.id, ME, "running")
    await before.next_messages(row.id, 1)                            # the runtime has it
    after = make()
    await after.load()
    assert await after.next_messages(row.id, 0.05) == []


async def test_a_session_that_never_got_its_pod_ends_at_load(make, db, runner):
    sessions = make()
    async with db.begin() as s:                                      # inserted, then we died before the claim
        c = Conversation(owner=ME, profile="voice")
        s.add(c)
        await s.flush()
        orphan = ChatSession(conversation_id=c.id, owner=ME, state=SessionState.STARTING)
        s.add(orphan)
    await sessions.load()
    assert (await state(db, orphan.id)).end_reason == EndReason.FAILED


# ---- the kill switch (C14) ------------------------------------------------------------------------
async def test_the_kill_switch(make, db, runner):
    sessions = make()
    a, _ = await ready_session(sessions)
    b, _ = await ready_session(sessions, "voice")
    assert await sessions.stop("service:agent-cli") == 2
    assert {(await state(db, x.id)).end_reason for x in (a, b)} == {EndReason.STOPPED}
    with pytest.raises(Stopped):
        await sessions.start(ME, "voice")
    restarted = make()
    await restarted.load()
    with pytest.raises(Stopped):                                     # it survives a restart
        await restarted.start(ME, "voice")
    await restarted.resume("service:agent-cli")
    row, created = await restarted.start(ME, "voice")
    assert created
