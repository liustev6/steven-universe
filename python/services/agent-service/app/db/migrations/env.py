"""Alembic's entry point (the async template): runs migrations against the URL app.db passes in."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def _migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    url = config.attributes.get("url")
    if url is None:                       # the `alembic` command line, inside the container
        from app.config import Settings

        url = Settings().database_url()
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_migrate)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) migrations aren't used here")
asyncio.run(_run())
