"""Readiness probes for backing services used by ``/readyz``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy import text

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from spoldzielnia_ai.core.settings import Settings

ReadinessCheck = Callable[[], Awaitable[bool]]

CHECK_TIMEOUT_SECONDS = 2.0

log = get_logger(__name__)


def postgres_check(engine: AsyncEngine) -> ReadinessCheck:
    async def check() -> bool:
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("readiness.postgres_failed", error=type(exc).__name__)
            return False
        return True

    return check


def migrations_check(engine: AsyncEngine, expected_revision: str) -> ReadinessCheck:
    """The database schema is at the Alembic head shipped with this image."""

    async def check() -> bool:
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), engine.connect() as conn:
                current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception as exc:
            log.warning("readiness.migrations_failed", error=type(exc).__name__)
            return False
        if current != expected_revision:
            log.warning("readiness.schema_mismatch", current=current, expected=expected_revision)
            return False
        return True

    return check


def redis_check(settings: Settings) -> ReadinessCheck:
    async def check() -> bool:
        client = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            settings.redis.url(), socket_timeout=CHECK_TIMEOUT_SECONDS
        )
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
                return bool(await client.ping())  # pyright: ignore[reportUnknownMemberType]
        except Exception as exc:
            log.warning("readiness.redis_failed", error=type(exc).__name__)
            return False
        finally:
            await client.aclose()

    return check


def default_checks(
    settings: Settings, engine: AsyncEngine, expected_revision: str
) -> dict[str, ReadinessCheck]:
    return {
        "postgres": postgres_check(engine),
        "migrations": migrations_check(engine, expected_revision),
        "redis": redis_check(settings),
    }
