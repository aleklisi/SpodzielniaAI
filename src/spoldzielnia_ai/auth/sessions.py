"""Server-side sessions (BFF) with refresh-token rotation and reuse detection.

Redis layout (keys of secrets are their SHA-256, so a Redis dump yields no usable cookie values):

- ``session:{H(sid)}``             → ``SessionData``, expires at the absolute session end
- ``family:{family_id}``           → ``FamilyData``: the current refresh token (sealed), the
                                     ID token for logout (sealed) and when to re-check the IdP
- ``family:{family_id}:sessions``  → keys of the sessions in this family
- ``rt:{H(refresh_token)}``        → ``{family_id, used}``. A used token presented again is
                                     treated as theft: the whole family is revoked.
- ``lock:family:{family_id}``      → per-family refresh lock, so parallel requests never
                                     present the same refresh token twice (no false positives)

One login creates one family. The access token is not kept, because the API calls no resource
servers. Its expiry only decides when to refresh, which re-checks with the IdP that the user's
session is still alive (IdP logout or disable takes effect within ``access_token_ttl_seconds``).

This module is also the typed adapter around ``redis.asyncio`` for these keys (conventions §2.2).
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID  # noqa: TC003 - pydantic resolves the annotation at runtime

from pydantic import BaseModel, ConfigDict, ValidationError

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.auth.crypto import TokenSealer, digest
from spoldzielnia_ai.auth.oidc_client import OidcTokenError
from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

    from pydantic import JsonValue
    from redis.asyncio import Redis

    from spoldzielnia_ai.auth.identity import OidcIdentity
    from spoldzielnia_ai.auth.oidc_client import OidcClient, TokenResponse
    from spoldzielnia_ai.core.settings import SecuritySettings

log = get_logger(__name__)

SID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")

_UNLOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end
return 0
"""


class SessionData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    family_id: str
    acr: str | None
    amr: list[str]
    auth_time: int | None
    created_at: int
    expires_at: int


class FamilyData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    refresh_token: str | None  # sealed
    id_token: str | None  # sealed, `id_token_hint` for RP-initiated logout
    access_expires_at: int
    expires_at: int


class _RefreshTokenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    family_id: str
    used: bool


class RefreshTokenReuseError(Exception):
    """A refresh token was presented after it had been rotated: the family is revoked."""


class SessionBackendUnavailableError(Exception):
    """The IdP or the refresh lock is unavailable. The session is kept and the request fails
    (503)."""


class AuditSink(Protocol):
    async def __call__(
        self, event: EventType, actor_id: UUID, payload: Mapping[str, JsonValue]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class NewSession:
    sid: str
    data: SessionData


def _session_key(sid: str) -> str:
    return f"session:{digest(sid)}"


def _family_key(family_id: str) -> str:
    return f"family:{family_id}"


def _members_key(family_id: str) -> str:
    return f"family:{family_id}:sessions"


def _rt_key(refresh_token: str) -> str:
    return f"rt:{digest(refresh_token)}"


class SessionStore:
    """Redis persistence only. Policy lives in ``SessionManager``."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def _get(self, key: str) -> bytes | str | None:
        return await self._redis.get(key)

    async def get_session(self, sid: str) -> SessionData | None:
        raw = await self._get(_session_key(sid))
        try:
            return SessionData.model_validate_json(raw) if raw is not None else None
        except ValidationError:
            return None

    async def get_family(self, family_id: str) -> FamilyData | None:
        raw = await self._get(_family_key(family_id))
        try:
            return FamilyData.model_validate_json(raw) if raw is not None else None
        except ValidationError:
            return None

    async def get_refresh_record(self, refresh_token: str) -> _RefreshTokenRecord | None:
        raw = await self._get(_rt_key(refresh_token))
        try:
            return _RefreshTokenRecord.model_validate_json(raw) if raw is not None else None
        except ValidationError:
            return None

    async def create(
        self,
        sid: str,
        session: SessionData,
        family: FamilyData,
        refresh_token: str | None,
    ) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(_session_key(sid), session.model_dump_json(), exat=session.expires_at)
        pipe.set(_family_key(session.family_id), family.model_dump_json(), exat=family.expires_at)
        pipe.sadd(_members_key(session.family_id), _session_key(sid))
        pipe.expireat(_members_key(session.family_id), family.expires_at)
        if refresh_token is not None:
            record = _RefreshTokenRecord(family_id=session.family_id, used=False)
            pipe.set(_rt_key(refresh_token), record.model_dump_json(), exat=family.expires_at)
        await pipe.execute()

    async def rotate(
        self, family_id: str, family: FamilyData, old_token: str, new_token: str
    ) -> None:
        """Mark ``old_token`` used and make ``new_token`` current, atomically."""
        used = _RefreshTokenRecord(family_id=family_id, used=True)
        fresh = _RefreshTokenRecord(family_id=family_id, used=False)
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(_rt_key(old_token), used.model_dump_json(), exat=family.expires_at)
        pipe.set(_rt_key(new_token), fresh.model_dump_json(), exat=family.expires_at)
        pipe.set(_family_key(family_id), family.model_dump_json(), exat=family.expires_at)
        await pipe.execute()

    async def save_family(self, family_id: str, family: FamilyData) -> None:
        await cast(
            "Awaitable[bool | None]",
            self._redis.set(
                _family_key(family_id), family.model_dump_json(), exat=family.expires_at
            ),
        )

    async def delete_session(self, sid: str) -> None:
        await self._redis.delete(_session_key(sid))

    async def delete_family(self, family_id: str) -> None:
        """Delete the family and every session in it. Refresh-token records stay, so a rotated
        token still reads as used."""
        members = await self._redis.smembers(_members_key(family_id))
        keys = [m.decode() if isinstance(m, bytes) else m for m in members]
        await self._redis.delete(_family_key(family_id), _members_key(family_id), *keys)

    @asynccontextmanager
    async def family_lock(self, family_id: str, wait_seconds: float) -> AsyncGenerator[None]:
        key = f"lock:family:{family_id}"
        token = secrets.token_hex(16)
        # The lock outlives a hung holder for at most this long.
        ttl_ms = int((wait_seconds + 5) * 1000)
        deadline = time.monotonic() + wait_seconds
        while not await cast(
            "Awaitable[bool | None]", self._redis.set(key, token, nx=True, px=ttl_ms)
        ):
            if time.monotonic() >= deadline:
                raise SessionBackendUnavailableError("refresh lock timeout")
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            await self._redis.eval(_UNLOCK, 1, key, token)


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        oidc: OidcClient,
        sealer: TokenSealer,
        settings: SecuritySettings,
        audit: AuditSink,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._oidc = oidc
        self._sealer = sealer
        self._settings = settings
        self._audit = audit
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    def _access_expiry(self, now: int, tokens: TokenResponse, session_end: int) -> int:
        ttl = self._settings.access_token_ttl_seconds
        if tokens.expires_in is not None:
            ttl = min(ttl, max(tokens.expires_in, 0))
        return min(now + ttl, session_end)

    async def create(
        self, user_id: UUID, identity: OidcIdentity, tokens: TokenResponse
    ) -> NewSession:
        """A new session (and token family) for a completed login. Always a fresh sid, so a
        session id planted before login is never promoted (session fixation)."""
        now = self._now()
        sid = secrets.token_urlsafe(32)
        family_id = secrets.token_urlsafe(16)
        expires_at = now + self._settings.session_absolute_ttl_seconds
        session = SessionData(
            user_id=user_id,
            family_id=family_id,
            acr=identity.acr,
            amr=list(identity.amr),
            auth_time=identity.auth_time,
            created_at=now,
            expires_at=expires_at,
        )
        family = FamilyData(
            user_id=user_id,
            refresh_token=(
                self._sealer.seal(tokens.refresh_token, family_id) if tokens.refresh_token else None
            ),
            id_token=self._sealer.seal(tokens.id_token, family_id) if tokens.id_token else None,
            access_expires_at=self._access_expiry(now, tokens, expires_at),
            expires_at=expires_at,
        )
        await self._store.create(sid, session, family, tokens.refresh_token)
        return NewSession(sid=sid, data=session)

    async def resolve(self, sid: str, client_ip: str | None = None) -> SessionData | None:
        """The live session for ``sid``, refreshed with the IdP when due, or ``None``.

        Raises ``RefreshTokenReuseError`` (family revoked) or ``SessionBackendUnavailableError``.
        """
        if not SID_PATTERN.fullmatch(sid):
            return None
        session = await self._store.get_session(sid)
        if session is None:
            return None
        now = self._now()
        if session.expires_at <= now:
            await self._store.delete_family(session.family_id)
            return None
        family = await self._store.get_family(session.family_id)
        if family is None:
            await self._store.delete_session(sid)
            return None
        if family.access_expires_at > now:
            return session
        return session if await self._refresh(session, client_ip) else None

    async def _refresh(self, session: SessionData, client_ip: str | None) -> bool:
        family_id = session.family_id
        async with self._store.family_lock(family_id, self._settings.refresh_lock_timeout_seconds):
            # Another request may have refreshed (or revoked) while we waited for the lock.
            family = await self._store.get_family(family_id)
            if family is None:
                return False
            now = self._now()
            if family.access_expires_at > now:
                return True

            refresh_token = (
                self._sealer.open(family.refresh_token, family_id) if family.refresh_token else None
            )
            if refresh_token is None:
                await self._store.delete_family(family_id)
                return False
            record = await self._store.get_refresh_record(refresh_token)
            if record is None or record.used or record.family_id != family_id:
                await self._revoke_for_reuse(session, client_ip)
                raise RefreshTokenReuseError(family_id)

            try:
                tokens = await self._oidc.refresh(refresh_token)
            except OidcTokenError as exc:
                log.info("session.idp_ended", family=family_id, error=str(exc))
                await self._store.delete_family(family_id)
                return False
            # OidcProviderError propagates: the IdP is down, the session is kept (→ 503).

            new_token = tokens.refresh_token or refresh_token
            updated = family.model_copy(
                update={
                    "refresh_token": self._sealer.seal(new_token, family_id),
                    "id_token": (
                        self._sealer.seal(tokens.id_token, family_id)
                        if tokens.id_token
                        else family.id_token
                    ),
                    "access_expires_at": self._access_expiry(now, tokens, family.expires_at),
                }
            )
            if new_token == refresh_token:
                # The realm rotates refresh tokens. Keep working if an IdP doesn't.
                await self._store.save_family(family_id, updated)
            else:
                await self._store.rotate(family_id, updated, refresh_token, new_token)
            return True

    async def _revoke_for_reuse(self, session: SessionData, client_ip: str | None) -> None:
        log.warning("session.refresh_token_reuse", family=session.family_id)
        await self._store.delete_family(session.family_id)
        await self._audit(
            EventType.REFRESH_TOKEN_REUSE,
            session.user_id,
            {"family": digest(session.family_id)[:16], "ip": client_ip},
        )

    async def revoke_family(self, session: SessionData) -> None:
        await self._store.delete_family(session.family_id)

    async def logout(self, sid: str) -> str | None:
        """End the session's whole family, revoke the refresh token at the IdP and return the
        RP-initiated logout URL for the browser (if any)."""
        session = await self._store.get_session(sid) if SID_PATTERN.fullmatch(sid) else None
        if session is None:
            return await self._oidc.end_session_url(None)
        family = await self._store.get_family(session.family_id)
        await self._store.delete_family(session.family_id)
        id_token: str | None = None
        if family is not None:
            if family.refresh_token and (
                token := self._sealer.open(family.refresh_token, session.family_id)
            ):
                await self._oidc.revoke(token)
            if family.id_token:
                id_token = self._sealer.open(family.id_token, session.family_id)
        await self._audit(EventType.USER_LOGOUT, session.user_id, {})
        return await self._oidc.end_session_url(id_token)
