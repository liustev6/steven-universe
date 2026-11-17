"""The event log (C4): numbered once per session, stored before it's seen, retries stored once."""

import asyncio
import uuid

import pytest

from app.history.eventlog import EventLog, NewEvent, UnknownSession


def text(t: str, source_seq: int | None = None, turn: uuid.UUID | None = None) -> NewEvent:
    return NewEvent("message.delta", {"text": t}, turn_id=turn, source_seq=source_seq)


async def test_numbered_from_zero_with_the_envelope(db, new_session):
    log, session, turn = EventLog(db), await new_session(), uuid.uuid4()
    stored = await log.append(session.id, [NewEvent("session.status", {"status": "ready"}), text("hi", 0, turn)])
    assert [e["seq"] for e in stored] == [0, 1]
    e = stored[1]
    assert e["v"] == 1 and e["type"] == "message.delta" and e["text"] == "hi"
    assert e["session_id"] == str(session.id) and e["conversation_id"] == str(session.conversation_id)
    assert e["turn_id"] == str(turn) and e["ts"].endswith("+00:00")
    assert stored[0]["turn_id"] is None
    assert await log.read(session.id) == stored            # what was returned is what was stored


async def test_seq_continues_across_appends_and_sessions_are_separate(db, new_session):
    log, a, b = EventLog(db), await new_session(), await new_session()
    await log.append(a.id, [text("1"), text("2")])
    await log.append(b.id, [text("x")])
    third = await log.append(a.id, [text("3")])
    assert third[0]["seq"] == 2
    assert [e["seq"] for e in await log.read(b.id)] == [0]
    assert [e["text"] for e in await log.read(a.id, after=0)] == ["2", "3"]


async def test_a_retried_batch_is_stored_once(db, new_session):
    """The runtime resends a batch whose response it lost: same protocol A seqs."""
    log, session = EventLog(db), await new_session()
    first = await log.append(session.id, [text("a", 0), text("b", 1)])
    again = await log.append(session.id, [text("a", 0), text("b", 1), text("c", 2)])
    assert [e["text"] for e in again] == ["c"]
    assert [e["seq"] for e in first + again] == [0, 1, 2]
    dup_in_batch = await log.append(session.id, [text("d", 3), text("d", 3)])
    assert len(dup_in_batch) == 1


async def test_concurrent_appends_never_share_a_seq(db, new_session):
    log, session = EventLog(db), await new_session()
    await asyncio.gather(*(log.append(session.id, [text(f"{i}a"), text(f"{i}b")]) for i in range(10)))
    seqs = [e["seq"] for e in await log.read(session.id)]
    assert seqs == list(range(20))


async def test_unknown_session(db):
    with pytest.raises(UnknownSession):
        await EventLog(db).append(uuid.uuid4(), [text("x")])


def test_event_data_cannot_forge_the_envelope():
    with pytest.raises(ValueError, match="seq"):
        NewEvent("message.delta", {"text": "x", "seq": 99})


async def test_follow_gives_the_backlog_then_live_events(db, new_session):
    log, session = EventLog(db), await new_session()
    await log.append(session.id, [text("old")])
    got: list = []

    async def listen():
        async for e in log.follow(session.id, heartbeat=5):
            got.append(e["text"])
            if len(got) == 3:
                return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.1)
    await log.append(session.id, [text("new1")])
    await log.append(session.id, [text("new2")])
    await asyncio.wait_for(listener, 5)
    assert got == ["old", "new1", "new2"]


async def test_follow_resumes_after_a_seq_and_heartbeats(db, new_session):
    """A reconnect: from `after`, nothing it already has; `None` when it's quiet."""
    log, session = EventLog(db), await new_session()
    await log.append(session.id, [text("a"), text("b"), text("c")])
    stream = log.follow(session.id, after=0, heartbeat=0.2)
    assert (await anext(stream))["text"] == "b"
    assert (await anext(stream))["text"] == "c"
    assert await anext(stream) is None
    await stream.aclose()


async def test_a_new_log_reads_what_an_old_one_stored(db, new_session):
    """agent-service restarted: memory is empty, the table isn't (C5)."""
    session = await new_session()
    await EventLog(db).append(session.id, [text("before restart")])
    fresh = EventLog(db)
    stream = fresh.follow(session.id, heartbeat=5)
    assert (await anext(stream))["text"] == "before restart"
    waiting = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)
    await fresh.append(session.id, [text("after restart")])
    assert (await asyncio.wait_for(waiting, 5))["text"] == "after restart"
    await stream.aclose()
