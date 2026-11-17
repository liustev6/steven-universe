"""The sessions core: each session's life, from claim to release (control-plane.md C5–C7, C9, C14).

    starting ──runtime.ready──▶ ready ◀──turn.completed── busy
       │  └──────────turn (queued until the runtime is ready)──▶ │
       │                          │ ──turn──────────────▶ │
       └──────────────┬───────────┴───────────────────────┘
                      ▼
                    ended    deleted · idle · wall_time · sandbox_lost · stopped · failed

Callers' side (the sessions API, piece e):
    start(owner, profile, conversation?)   the conversation's live session, or a new one with a pod
    turn(session, owner, text)             queue a turn for the runtime; returns where to follow from
    cancel(session, owner)                 interrupt the running turn
    end(session, reason)                   for good: release the pod, tell listeners

The runtime's side (the gateway, piece d):
    live_for(capability)                   which session a request belongs to
    next_messages(session, wait)           the long poll: turns, interrupts, end
    ingest(session, events)                protocol A in → protocol B stored, and the state it implies

Keeping it right:
- PostgreSQL is the truth (C5). Memory holds only what can be rebuilt: each live session's
  outbox to its runtime and its translator. `load()` rebuilds them at start.
- A state change and the events that announce it commit together (EventLog.write).
- Timers (idle, wall time, ready timeout) and lost sandboxes are found by `sweep()`, which reads
  the table, so a restart doesn't reset anyone's timer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.protocol import events as b
from app.config import Settings
from app.db.models import ChatSession, Conversation, EndReason, Flag, SessionState, Turn, TurnStatus
from app.history.carry import context_text, recent_turns
from app.history.eventlog import EventLog
from app.profiles import AgentProfile
from app.protocol.translate import Translator
from app.sessions.errors import (
    Busy, BudgetExceeded, Ended, NotFound, ProfileMismatch, Stopped, UnknownProfile,
)
from app.sessions.runner import NoSandbox, Runner, RunnerError

logger = logging.getLogger(__name__)

LIVE = (SessionState.STARTING, SessionState.READY, SessionState.BUSY)
KILL_SWITCH = "stopped"


def capability_hash(capability: str) -> bytes:
    return hashlib.sha256(capability.encode()).digest()


@dataclass
class Live:
    """What memory holds for a live session; rebuilt from the table by `load()`."""
    session_id: uuid.UUID
    profile: str
    sandbox_id: uuid.UUID
    capability_hash: bytes
    translator: Translator
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)   # protocol A messages to the runtime
    turn_id: uuid.UUID | None = None                               # the running turn
    turn_tokens: int = 0              # the running turn's tokens, counted by the gateway (C13)
    over_budget: bool = False         # the running turn hit max_turn_tokens (reported once)


class Sessions:
    def __init__(self, db: async_sessionmaker, log: EventLog, runner: Runner,
                 profiles: dict[str, AgentProfile], settings: Settings):
        self.db, self.log, self.runner, self.profiles, self.s = db, log, runner, profiles, settings
        self._live: dict[uuid.UUID, Live] = {}
        self._by_hash: dict[bytes, Live] = {}          # the gateway's lookup (C7)
        self.stopped = False

    # ==== start-up (C5) ===========================================================================
    async def load(self) -> None:
        """Rebuild memory for every live session after a (re)start. Their runtimes' long polls simply
        reconnect; nothing they sent was lost (it's in the table)."""
        async with self.db() as db:
            flag = await db.get(Flag, KILL_SWITCH)
            self.stopped = bool(flag and flag.value)
            rows = (await db.execute(select(ChatSession, Conversation.profile).join(Conversation)
                                     .where(ChatSession.state.in_(LIVE)))).all()
            running = {t.session_id: t for t in await db.scalars(
                select(Turn).where(Turn.session_id.in_([r.id for r, _ in rows]), Turn.status == TurnStatus.RUNNING))}
        for row, profile in rows:
            if row.sandbox_id is None or row.capability_hash is None:   # we died before its pod was claimed
                await self.end(row.id, EndReason.FAILED)
                continue
            live = Live(row.id, profile, row.sandbox_id, row.capability_hash, Translator(row.id, fresh=False))
            turn = running.get(row.id)
            if turn is not None:
                live.turn_id = turn.id
                if turn.delivered_at is None:        # queued but never picked up: send it again
                    live.outbox.put_nowait({"type": "turn", "turn_id": str(turn.id), "text": turn.user_text})
            self._remember(live)
        logger.info("loaded %d live session(s)%s", len(self._live), "; the kill switch is ON" if self.stopped else "")

    # ==== callers' side ===========================================================================
    async def start(self, owner: str, profile: str,
                    conversation_id: uuid.UUID | None = None) -> tuple[ChatSession, bool]:
        """The conversation's live session (False), or a new session with a claimed pod (True)."""
        p = self.profiles.get(profile)
        if p is None:
            raise UnknownProfile(f"unknown profile: {profile}")
        if self.stopped:
            raise Stopped("agent-service is stopped (the kill switch)")
        capability = secrets.token_urlsafe(32)                  # 256 random bits (C7)
        try:
            async with self.db.begin() as db:
                if conversation_id is not None:
                    conversation = await self._owned_conversation(db, conversation_id, owner)
                    if conversation.profile != profile:
                        raise ProfileMismatch(f"this conversation uses the profile {conversation.profile!r}")
                    live = await self._live_row(db, conversation_id)
                    if live is not None:
                        return live, False
                else:
                    conversation = Conversation(owner=owner, profile=profile)
                    db.add(conversation)
                    await db.flush()
                session = ChatSession(conversation_id=conversation.id, owner=owner, state=SessionState.STARTING,
                                      capability_hash=capability_hash(capability))
                db.add(session)
                await db.flush()     # the one-live-session index decides a race here (C6)
                carried = await recent_turns(db, conversation.id, p.history.carry_turns) if conversation_id else []
        except IntegrityError:
            async with self.db() as db:                         # another start won: use its session
                live = await self._live_row(db, conversation_id)
            if live is None:
                raise
            return live, False

        runtime = p.runtime(profile, context_text(carried, self.s.CARRY_CHARS))
        try:
            sandbox_id = await self.runner.claim(p.template, session.id, capability, runtime.model_dump(mode="json"))
        except (NoSandbox, RunnerError):
            await self._discard(session, new_conversation=conversation_id is None)
            raise
        async with self.db.begin() as db:
            row = await db.get(ChatSession, session.id, with_for_update=True)
            row.sandbox_id = sandbox_id
            still_live = row.state != SessionState.ENDED
        if not still_live:                                      # ended while we claimed (the kill switch)
            await self.runner.release(sandbox_id)
            return row, True
        self._remember(Live(row.id, profile, sandbox_id, capability_hash(capability), Translator(row.id)))
        logger.info("session %s started (profile %s, sandbox %s, %d turn(s) carried)", row.id, profile,
                    sandbox_id, len(carried))
        return row, True

    async def get(self, session_id: uuid.UUID, owner: str | None) -> ChatSession:
        """`owner=None`: any session (chat:admin)."""
        async with self.db() as db:
            row = await db.get(ChatSession, session_id)
        if row is None or (owner is not None and row.owner != owner):
            raise NotFound("no such session")
        return row

    async def turn(self, session_id: uuid.UUID, owner: str | None, text: str) -> tuple[Turn, int]:
        """Queue a turn. Returns it and the last stored `seq`: the caller follows the events after it."""
        async with self.db.begin() as db:
            row = await self._owned_session(db, session_id, owner)
            if row.state == SessionState.ENDED:
                raise Ended(f"the session has ended ({row.end_reason}); start a new one in the conversation")
            if row.state == SessionState.BUSY:
                raise Busy("a turn is already running")
            p = self.profiles.get((await db.get(Conversation, row.conversation_id)).profile)
            if p is not None and row.turns_used >= p.limits.max_turns:
                raise BudgetExceeded(f"this session has used its {p.limits.max_turns} turns")
            now = datetime.now(UTC)
            turn = Turn(id=uuid.uuid4(), conversation_id=row.conversation_id, session_id=row.id, user_text=text,
                        status=TurnStatus.RUNNING)
            db.add(turn)
            row.state = SessionState.BUSY
            row.turns_used += 1
            row.last_activity_at = now
            await db.execute(update(Conversation).where(Conversation.id == row.conversation_id)
                             .values(last_active_at=now))
            after = row.next_seq - 1
        live = self._live.get(session_id)
        if live is not None:
            live.turn_id, live.turn_tokens, live.over_budget = turn.id, 0, False
            live.outbox.put_nowait({"type": "turn", "turn_id": str(turn.id), "text": text})
        return turn, after

    async def cancel(self, session_id: uuid.UUID, owner: str | None) -> bool:
        """Interrupt the running turn; it still ends with turn.completed (status interrupted)."""
        row = await self.get(session_id, owner)
        live = self._live.get(session_id)
        if row.state != SessionState.BUSY or live is None or live.turn_id is None:
            return False
        live.outbox.put_nowait({"type": "interrupt", "turn_id": str(live.turn_id)})
        return True

    async def end(self, session_id: uuid.UUID, reason: EndReason, owner: str | None = None) -> bool:
        """End it for good: state, a running turn and the announcement commit together; then the
        runtime is told and the pod released. False if it had already ended."""
        async with self.db.begin() as db:
            row = await self._owned_session(db, session_id, owner)
            if row.state == SessionState.ENDED:
                return False
            now = datetime.now(UTC)
            row.state, row.end_reason, row.ended_at = SessionState.ENDED, reason, now
            row.capability_hash = None                          # dies with the session (C7)
            await db.execute(update(Turn).where(Turn.session_id == row.id, Turn.status == TurnStatus.RUNNING)
                             .values(status=TurnStatus.FAILED, reason=f"session ended: {reason.value}",
                                     completed_at=now))
            stored = await self.log.write(db, row, [b.new_event(b.SessionStatus(status="ended", reason=reason))])
            sandbox_id = row.sandbox_id
        await self.log.publish(session_id, stored)
        live = self._live.pop(session_id, None)
        if live is not None:
            self._by_hash.pop(live.capability_hash, None)
            live.outbox.put_nowait({"type": "end"})
        if sandbox_id is not None:
            await self.runner.release(sandbox_id)
        logger.info("session %s ended: %s", session_id, reason.value)
        return True

    # ==== the runtime's side (the gateway) ========================================================
    def live_for(self, capability: str) -> Live | None:
        """The live session holding this capability, or None. The gateway also checks the pod (C7).
        From memory: every request of every runtime asks, and `load()` rebuilds it from the table."""
        return self._by_hash.get(capability_hash(capability))

    async def next_messages(self, session_id: uuid.UUID, wait: float) -> list[dict[str, Any]]:
        """The runtime's long poll: whatever is queued, waiting up to `wait` seconds for the first."""
        live = self._live.get(session_id)
        if live is None:
            return [{"type": "end"}]
        try:
            first = await asyncio.wait_for(live.outbox.get(), timeout=max(0.0, min(wait, self.s.POLL_WAIT_MAX)))
        except TimeoutError:
            return []
        messages = [first]
        while not live.outbox.empty():
            messages.append(live.outbox.get_nowait())
        delivered = [uuid.UUID(m["turn_id"]) for m in messages if m["type"] == "turn"]
        if delivered:
            async with self.db.begin() as db:
                await db.execute(update(Turn).where(Turn.id.in_(delivered)).values(delivered_at=datetime.now(UTC)))
        return messages

    async def ingest(self, session_id: uuid.UUID, raw_events: list[dict[str, Any]]) -> int:
        """Protocol A from the runtime: translated, stored, and the state it implies (ready, a turn
        finished) committed in the same transaction. Returns how many events were dropped."""
        live = self._live.get(session_id)
        if live is None:                                        # ended: late events are dropped
            return len(raw_events)
        tr = live.translator.translate(raw_events)
        async with self.db.begin() as db:
            row = await db.get(ChatSession, session_id, with_for_update=True)
            if row is None or row.state == SessionState.ENDED:
                return len(raw_events)
            now = datetime.now(UTC)
            extra: list = []
            if tr.ready and row.ready_at is None:
                row.ready_at = now
                if row.state == SessionState.STARTING:      # else busy: a turn was queued while starting
                    row.state = SessionState.READY
                extra.append(b.new_event(b.SessionStatus(status="ready")))
            for done in tr.turns_done:
                result = await db.execute(
                    update(Turn).where(Turn.id == done.turn_id, Turn.session_id == row.id,
                                       Turn.status == TurnStatus.RUNNING)
                    .values(status=TurnStatus(done.status), reason=done.reason, reply_text=done.reply_text,
                            first_text_ms=done.first_text_ms, duration_ms=done.duration_ms, usage=done.usage,
                            completed_at=now))
                if result.rowcount and live.turn_id == done.turn_id:
                    live.turn_id = None
                    row.state = SessionState.READY
                    row.last_activity_at = now
            stored = await self.log.write(db, row, tr.events + extra)
        await self.log.publish(session_id, stored)
        return tr.dropped

    # ==== timers and lost sandboxes ===============================================================
    async def sweep(self) -> None:
        """End what has to end: never ready, wall time, idle, or a pod the runner no longer has."""
        now = datetime.now(UTC)
        async with self.db() as db:
            rows = (await db.execute(
                select(ChatSession, Conversation.profile).join(Conversation)
                .where(ChatSession.state.in_(LIVE)))).all()
        # Read the runner's pods *after* the rows: a session claimed in between is in our rows with
        # a sandbox the (newer) list includes, never judged lost by a stale list.
        claimed = await self.runner.claimed() if rows else set()
        for row, profile_name in rows:
            p = self.profiles.get(profile_name)
            reason = None
            if p is None:
                logger.warning("session %s: its profile %r is gone from profiles.yaml", row.id, profile_name)
                reason = EndReason.FAILED
            elif row.ready_at is None and now - row.started_at > timedelta(seconds=self.s.READY_TIMEOUT_SECONDS):
                reason = EndReason.FAILED
            elif now - row.started_at > timedelta(seconds=p.limits.wall_seconds):
                reason = EndReason.WALL_TIME
            elif row.state == SessionState.READY and now - row.last_activity_at > timedelta(seconds=self.s.IDLE_SECONDS):
                reason = EndReason.IDLE
            elif claimed is not None and row.sandbox_id is not None and row.sandbox_id not in claimed:
                reason = EndReason.SANDBOX_LOST
            if reason is not None:
                await self.end(row.id, reason)

    async def run_sweeper(self) -> None:
        while True:
            await asyncio.sleep(self.s.SWEEP_SECONDS)
            try:
                await self.sweep()
            except Exception:                                   # the next sweep tries again
                logger.exception("sweep failed")

    # ==== the kill switch (C14) ===================================================================
    async def stop(self, by: str) -> int:
        """Refuse new sessions (across restarts) and end every live one. Returns how many ended."""
        await self._set_flag(KILL_SWITCH, True, by)
        self.stopped = True
        async with self.db() as db:
            ids = (await db.scalars(select(ChatSession.id).where(ChatSession.state.in_(LIVE)))).all()
        ended = 0
        for session_id in ids:
            ended += await self.end(session_id, EndReason.STOPPED)
        logger.warning("kill switch ON (by %s): %d session(s) ended", by, ended)
        return ended

    async def resume(self, by: str) -> None:
        await self._set_flag(KILL_SWITCH, False, by)
        self.stopped = False
        logger.warning("kill switch OFF (by %s)", by)

    # ==== helpers =================================================================================
    def _remember(self, live: Live) -> None:
        self._live[live.session_id] = live
        self._by_hash[live.capability_hash] = live

    async def _set_flag(self, name: str, value: bool, by: str) -> None:
        async with self.db.begin() as db:
            flag = await db.get(Flag, name)
            if flag is None:
                db.add(Flag(name=name, value=value, changed_by=by))
            else:
                flag.value, flag.changed_by = value, by

    async def _owned_conversation(self, db: AsyncSession, conversation_id: uuid.UUID, owner: str) -> Conversation:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None or conversation.owner != owner:
            raise NotFound("no such conversation")
        return conversation

    async def _owned_session(self, db: AsyncSession, session_id: uuid.UUID, owner: str | None) -> ChatSession:
        row = await db.get(ChatSession, session_id, with_for_update=True)
        if row is None or (owner is not None and row.owner != owner):
            raise NotFound("no such session")
        return row

    async def _live_row(self, db: AsyncSession, conversation_id: uuid.UUID | None) -> ChatSession | None:
        if conversation_id is None:
            return None
        return await db.scalar(select(ChatSession).where(
            ChatSession.conversation_id == conversation_id, ChatSession.state.in_(LIVE)))

    async def _discard(self, session: ChatSession, new_conversation: bool) -> None:
        """A start that got no pod leaves nothing behind: the caller gets 503 and tries again."""
        async with self.db.begin() as db:
            await db.execute(delete(ChatSession).where(ChatSession.id == session.id))
            if new_conversation:
                await db.execute(delete(Conversation).where(Conversation.id == session.conversation_id))
