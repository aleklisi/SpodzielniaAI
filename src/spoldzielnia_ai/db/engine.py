"""Async engine for the runtime role (``spoldzielnia_app``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import DbSettings


def create_app_engine(db: DbSettings) -> AsyncEngine:
    """Engine using the app DSN only. The owner DSN is never available to the API process.

    Creating the engine opens no connection; the pool connects on first use.
    """
    return create_async_engine(
        db.app_url(),
        pool_size=db.pool_size,
        max_overflow=db.pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
