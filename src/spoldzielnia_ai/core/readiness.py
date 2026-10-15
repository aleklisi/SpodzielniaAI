"""Readiness probes for backing services used by ``/readyz``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings

ReadinessCheck = Callable[[], Awaitable[bool]]

CHECK_TIMEOUT_SECONDS = 2.0

log = get_logger(__name__)


def postgres_check(settings: Settings) -> ReadinessCheck:
    async def check() -> bool:
        engine = create_async_engine(settings.db.app_url(), pool_pre_ping=False)
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS), engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            log.warning("readiness.postgres_failed", error=type(exc).__name__)
            return False
        finally:
            await engine.dispose()
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


def default_checks(settings: Settings) -> dict[str, ReadinessCheck]:
    return {"postgres": postgres_check(settings), "redis": redis_check(settings)}
