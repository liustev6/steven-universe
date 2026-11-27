"""The history (control-plane.md "History"): four tables in agent-service's own PostgreSQL.

    conversations   what a caller is talking about, with one profile; outlives its sessions
    sessions        one sandbox's life within a conversation (C6), at most one live at a time:
                    ownership, the capability's hash,
                    the protocol B counter (`next_seq`), the budgets used
    turns           one question and its answer: the record that carries into new sessions (C9)
    events          every protocol B event, written before anyone sees it (C4); kept forever
    flags           service-wide switches that must survive a restart: the kill switch (C14)

Schema changes are Alembic migrations (app/db/migrations/), reviewed in git; a test checks that
these models and the migrations agree.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger, DateTime, Enum, ForeignKey, Index, Integer, LargeBinary, MetaData, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from shared_schemas.agent_service import EndReason  # noqa: F401  (part of protocol B: session.status)

# Predictable constraint names (Alembic's recommended convention), so migrations can name them.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


class SessionState(StrEnum):
    STARTING = "starting"      # claiming a pod; waiting for the runtime's runtime.ready (a turn may
                               # already be queued: then it's busy, and `ready_at` tells the two apart)
    READY = "ready"            # waiting for a turn
    BUSY = "busy"              # a turn is running
    ENDED = "ended"            # for good; `end_reason` says why




class TurnStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


def _enum(cls: type[StrEnum], name: str) -> Enum:
    """Stored as text with a CHECK constraint (not a PostgreSQL enum type): adding a value is a
    plain migration."""
    return Enum(cls, name=name, native_enum=False, create_constraint=True, length=16,
                values_callable=lambda e: [m.value for m in e])


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner: Mapped[str] = mapped_column(String(200), index=True)   # the token's subject (C2), e.g. service:agent-cli
    profile: Mapped[str] = mapped_column(String(32))              # fixed: every session in it uses this profile
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChatSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # One live session per conversation (C6): continuing a conversation returns its live session
        # instead of claiming a second pod. Inserted as `starting` before the claim, so of two racing
        # starts only one row (and one claim) wins.
        Index("uq_sessions_live_conversation", "conversation_id", unique=True,
              postgresql_where="state <> 'ended'"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    owner: Mapped[str] = mapped_column(String(200), index=True)   # the conversation's owner (C2)
    state: Mapped[SessionState] = mapped_column(_enum(SessionState, "session_state"), index=True)
    end_reason: Mapped[EndReason | None] = mapped_column(_enum(EndReason, "end_reason"))
    sandbox_id: Mapped[uuid.UUID | None]
    # SHA-256 of the capability (C7): the capability itself is only ever in the sandbox's session file.
    capability_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    instructions: Mapped[str | None] = mapped_column(Text)        # the caller's, if any (C15): what it was told
    next_seq: Mapped[int] = mapped_column(BigInteger, default=0)  # the next protocol B seq (one per session)
    turns_used: Mapped[int] = mapped_column(Integer, default=0)   # for max_turns (C13)
    # tools-service's tools for this session, as its tools/list answered at the start (tools.md TL7):
    # what the gateway's /mcp lists, fixed for the session. None or []: no tools.
    tools: Mapped[list[Any] | None] = mapped_column(JSONB)
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # max_tool_calls (TL10)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ready_at: Mapped[datetime | None]                             # runtime.ready arrived (startup time)
    last_activity_at: Mapped[datetime] = mapped_column(server_default=func.now())  # the idle timer (C6)
    ended_at: Mapped[datetime | None]


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_conversation_started", "conversation_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    user_text: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TurnStatus] = mapped_column(_enum(TurnStatus, "turn_status"))
    reason: Mapped[str | None] = mapped_column(String(200))
    first_text_ms: Mapped[int | None]
    duration_ms: Mapped[int | None]
    usage: Mapped[dict[str, Any] | None]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # When the runtime's long poll picked it up. After a restart, a running turn that was never
    # delivered is sent again; a delivered one isn't (the runtime would refuse it as "busy").
    delivered_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class Event(Base):
    """One protocol B event. `payload` is exactly what callers receive (envelope included)."""

    __tablename__ = "events"
    __table_args__ = (
        # A runtime retries a batch whose response it didn't get (core/gateway.py): its events are
        # stored once, by protocol A's seq. agent-service's own events have none.
        Index("uq_events_session_source_seq", "session_id", "source_seq", unique=True,
              postgresql_where="source_seq IS NOT NULL"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    turn_id: Mapped[uuid.UUID | None]
    source_seq: Mapped[int | None] = mapped_column(BigInteger)
    ts: Mapped[datetime]
    payload: Mapped[dict[str, Any]]


class Flag(Base):
    """A service-wide switch. `stopped` is the kill switch (C14): set, no session starts, and it
    stays set across restarts and deploys until someone resumes."""

    __tablename__ = "flags"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[bool]
    changed_by: Mapped[str] = mapped_column(String(200))
    changed_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
