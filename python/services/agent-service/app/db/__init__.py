"""The database: SQLAlchemy 2 (async) with psycopg 3, migrated by Alembic.

    make_engine(url)        one connection pool for the process
    make_sessionmaker(e)    `async with db() as s:` / `async with db.begin() as s:`
    upgrade(url)            apply every migration (at start, and in tests)
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

MIGRATIONS = Path(__file__).parent / "migrations"


def make_engine(url: URL | str) -> AsyncEngine:
    # A small pool: one process, a handful of concurrent sessions. pre_ping replaces a connection
    # PostgreSQL dropped (a restart of the database container) instead of failing a request.
    return create_async_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    # expire_on_commit=False: rows stay readable after their transaction (we hand them to route code).
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_config(url: URL | str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    # Passed as an object, not through the ini file: the URL holds the password, and configparser
    # would also mangle a '%' in it.
    cfg.attributes["url"] = url
    return cfg


def upgrade(url: URL | str) -> None:
    """Bring the schema to the latest migration. Synchronous (Alembic's API); run it in a thread
    from async code. Safe to repeat: applied migrations are skipped."""
    from alembic import command

    command.upgrade(alembic_config(url), "head")
