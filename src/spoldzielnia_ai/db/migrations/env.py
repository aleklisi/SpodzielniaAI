"""Alembic environment: async engine, owner role, constraint naming from ``Base.metadata``."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.settings import MigrateSettings
from spoldzielnia_ai.db.models import Base  # the package import registers every table

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url")
    if isinstance(url, str):
        return url
    settings = MigrateSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    return settings.db.migration_url()


def _configure(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_configure)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=_url(), target_metadata=target_metadata, compare_type=True, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
