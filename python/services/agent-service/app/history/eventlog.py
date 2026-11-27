"""The event log (control-plane.md C4): every protocol B event is written to PostgreSQL before
anyone sees it, then handed to listeners from memory.

    append(session, events)        number them (one `seq` per session) and store them, in one
                                   transaction; a runtime's retried events are stored once
    write(db, session, events)     the same, inside a caller's transaction; then publish()
    read(session, after)           the stored events after `seq`, oldest first
    follow(session, after)         read, then wait for more: what a stream or a reconnect uses

Numbering: `sessions.next_seq`, read and bumped under a row lock (SELECT … FOR UPDATE), so two
concurrent appends to one session can't take the same numbers. `seq` only goes up; callers must
accept gaps (C3), though today there are none.

Listeners wait on an in-memory signal per session, raised after each commit. A listener that
missed the signal (it connected later, or agent-service restarted) loses nothing: it always reads
from the table first, and the table is the truth.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared_schemas.agent_service import ENVELOPE_FIELDS

from app.db.models import ChatSession, Event

PROTOCOL_VERSION = 1
ENVELOPE = ENVELOPE_FIELDS                  # protocol B's envelope (shared_schemas.agent_service)


class UnknownSession(LookupError):
    pass


@dataclass(frozen=True)
class NewEvent:
    """An event before it's numbered: its type and its own fields (protocol B, no envelope)."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    turn_id: uuid.UUID | None = None
    source_seq: int | None = None      # protocol A's seq, for events from the runtime (deduplication)
    ts: datetime | None = None         # when it happened; default: now

    def __post_init__(self) -> None:
        clash = ENVELOPE & self.data.keys()
        if clash:
            raise ValueError(f"event data may not set envelope fields: {sorted(clash)}")


class EventLog:
    def __init__(self, db: async_sessionmaker):
        self._db = db
        self._latest: dict[uuid.UUID, int] = {}            # the last stored seq, per session (memory)
        self._signals: dict[uuid.UUID, asyncio.Condition] = {}

    # ---- writing ---------------------------------------------------------------------------------
    async def append(self, session_id: uuid.UUID, events: Sequence[NewEvent]) -> list[dict[str, Any]]:
        """Store `events` in their own transaction and publish them. See `write`."""
        if not events:
            return []
        async with self._db.begin() as db:
            session = await db.get(ChatSession, session_id, with_for_update=True)
            if session is None:
                raise UnknownSession(str(session_id))
            stored = await self.write(db, session, events)
        await self.publish(session_id, stored)
        return stored

    async def write(self, db: AsyncSession, session: ChatSession, events: Sequence[NewEvent]) -> list[dict[str, Any]]:
        """Number and add `events` inside the caller's transaction, so they commit together with the
        caller's other changes (a turn's row, the session's state) or not at all. `session` must be
        locked (SELECT … FOR UPDATE). Events whose `source_seq` is already stored for this session
        are skipped: a retried batch. Call `publish` after the commit."""
        stored: list[dict[str, Any]] = []
        sources = [e.source_seq for e in events if e.source_seq is not None]
        seen: set[int] = set()
        if sources:
            seen = set(await db.scalars(
                select(Event.source_seq).where(Event.session_id == session.id, Event.source_seq.in_(sources))))
        for e in events:
            if e.source_seq is not None:
                if e.source_seq in seen:
                    continue
                seen.add(e.source_seq)
            seq = session.next_seq
            session.next_seq += 1
            ts = e.ts or datetime.now(UTC)
            payload = {
                "v": PROTOCOL_VERSION, "session_id": str(session.id),
                "conversation_id": str(session.conversation_id),
                "turn_id": str(e.turn_id) if e.turn_id else None,
                "seq": seq, "ts": ts.isoformat(), "type": e.type, **e.data,
            }
            db.add(Event(session_id=session.id, seq=seq, type=e.type, turn_id=e.turn_id,
                         source_seq=e.source_seq, ts=ts, payload=payload))
            stored.append(payload)
        return stored

    async def publish(self, session_id: uuid.UUID, stored: list[dict[str, Any]]) -> None:
        """After the commit: wake this session's listeners."""
        if stored:
            await self._signal(session_id, stored[-1]["seq"])

    async def _signal(self, session_id: uuid.UUID, last: int) -> None:
        self._latest[session_id] = max(last, self._latest.get(session_id, -1))
        cond = self._signals.get(session_id)
        if cond is not None:
            async with cond:
                cond.notify_all()

    # ---- reading ---------------------------------------------------------------------------------
    async def read(self, session_id: uuid.UUID, after: int = -1, limit: int = 1000) -> list[dict[str, Any]]:
        async with self._db() as db:
            rows = await db.scalars(
                select(Event.payload).where(Event.session_id == session_id, Event.seq > after)
                .order_by(Event.seq).limit(limit))
            return list(rows)

    async def follow(self, session_id: uuid.UUID, after: int = -1, *,
                     heartbeat: float = 15.0) -> AsyncIterator[dict[str, Any] | None]:
        """Every event after `after`, then new ones as they're stored; `None` after `heartbeat`
        seconds without one (so a stream can send a keep-alive). The caller decides when to stop."""
        cond = self._signals.setdefault(session_id, asyncio.Condition())
        while True:
            batch = await self.read(session_id, after)
            for event in batch:
                after = event["seq"]
                yield event
            if len(batch) == 1000:         # more stored than one read returns: keep reading
                continue
            async with cond:            # released while waiting; never held while yielding
                try:
                    await asyncio.wait_for(
                        cond.wait_for(lambda: self._latest.get(session_id, -1) > after), heartbeat)
                    quiet = False
                except TimeoutError:
                    quiet = True
            if quiet:
                yield None

    def forget(self, session_id: uuid.UUID) -> None:
        """Drop a finished session's in-memory signal (its events stay in the table)."""
        self._latest.pop(session_id, None)
        self._signals.pop(session_id, None)
