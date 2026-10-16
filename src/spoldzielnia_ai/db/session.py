"""Session factory and the FastAPI dependency for non-private tables.

Private-zone tables (``private_note`` …) are accessed only through ``db.rls.rls_session`` /
``RlsSessionDep``. With a session from here, they return no rows and reject every insert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncEngine

SessionFactory = async_sessionmaker[AsyncSession]


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, from the factory created by ``create_app``."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        yield session
