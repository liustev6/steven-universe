"""The migrations: they apply to an empty PostgreSQL 17 (conftest), and they match the models."""

import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.db import upgrade
from app.db.models import Base, ChatSession, Event, SessionState


def test_models_and_migrations_agree(database_url):
    """What `alembic check` does: a model change without a migration fails here."""
    engine = create_engine(database_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == []


def test_upgrade_is_safe_to_repeat(database_url):
    upgrade(database_url)          # at every start: applied migrations are skipped


async def test_enum_columns_reject_unknown_values(db, new_session):
    session = await new_session()
    with pytest.raises(IntegrityError):
        async with db.begin() as s:
            await s.execute(text("UPDATE sessions SET state = 'sleeping' WHERE id = :id"), {"id": session.id})


async def test_a_capability_hash_belongs_to_one_session(db, new_session):
    a, b = await new_session(), await new_session()
    h = bytes(32)
    async with db.begin() as s:
        (await s.get(ChatSession, a.id)).capability_hash = h
    with pytest.raises(IntegrityError):
        async with db.begin() as s:
            (await s.get(ChatSession, b.id)).capability_hash = h


async def test_one_live_session_per_conversation(db, new_session):
    """C6: a second live session in a conversation is refused by the database itself; once the
    first has ended, a new one may start (and so on, for the conversation's whole life)."""
    first = await new_session()

    def second(state=SessionState.STARTING):
        return ChatSession(id=uuid.uuid4(), conversation_id=first.conversation_id, owner=first.owner, state=state)

    with pytest.raises(IntegrityError):
        async with db.begin() as s:
            s.add(second())
    async with db.begin() as s:
        (await s.get(ChatSession, first.id)).state = SessionState.ENDED
    async with db.begin() as s:
        s.add(second())                                 # the first has ended: allowed
    async with db.begin() as s:
        s.add(second(SessionState.ENDED))               # ended rows never conflict


async def test_events_need_their_session(db):
    from datetime import UTC, datetime

    with pytest.raises(IntegrityError):
        async with db.begin() as s:
            s.add(Event(session_id=uuid.uuid4(), seq=0, type="x", ts=datetime.now(UTC), payload={}))


def test_session_states_are_the_specs():
    assert [s.value for s in SessionState] == ["starting", "ready", "busy", "ended"]
