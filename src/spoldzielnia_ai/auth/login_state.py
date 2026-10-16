"""Single-use, short-lived login state in Redis: ``oidc:state:{state}``.

This module is the typed adapter around ``redis.asyncio`` for login state (conventions §2.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from spoldzielnia_ai.auth.pkce import TOKEN_PATTERN

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from redis.asyncio import Redis

KEY_PREFIX = "oidc:state:"


class LoginState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code_verifier: str
    nonce: str
    return_to: str


class LoginStateStore:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def save(self, state: str, login: LoginState) -> None:
        # NX: a (practically impossible) state collision must never overwrite another login.
        stored = await cast(
            "Awaitable[bool | None]",
            self._redis.set(KEY_PREFIX + state, login.model_dump_json(), ex=self._ttl, nx=True),
        )
        if not stored:
            raise RuntimeError("login state collision")

    async def consume(self, state: str) -> LoginState | None:
        """Return and delete the state in one step (GETDEL). A replayed state yields ``None``."""
        if not TOKEN_PATTERN.fullmatch(state):
            return None
        raw = await self._redis.getdel(KEY_PREFIX + state)
        if raw is None:
            return None
        try:
            return LoginState.model_validate_json(raw)
        except ValidationError:
            return None
