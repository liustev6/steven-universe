"""A real PostgreSQL 17 for the tests (testcontainers; needs Docker), migrated with the service's
own Alembic migrations, and emptied between tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db import make_engine, make_sessionmaker, upgrade
from app.db.models import ChatSession, Conversation, SessionState


@pytest.fixture(scope="session")
def database_url():
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine", driver="psycopg") as pg:
        url = pg.get_connection_url()
        upgrade(url)                  # the real migrations, not create_all: they're what production runs
        yield url


@pytest.fixture
async def db(database_url):
    engine = make_engine(database_url)
    yield make_sessionmaker(engine)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE events, turns, sessions, conversations"))
    await engine.dispose()


@pytest.fixture
def new_session(db):
    """A conversation and a session in it, as the sessions core will create them (piece c)."""
    async def make(owner: str = "service:agent-cli", profile: str = "smoke") -> ChatSession:
        async with db.begin() as s:
            conversation = Conversation(owner=owner, profile=profile)
            s.add(conversation)
            await s.flush()
            session = ChatSession(id=uuid.uuid4(), conversation_id=conversation.id, owner=owner,
                                  state=SessionState.READY)
            s.add(session)
        return session
    return make
