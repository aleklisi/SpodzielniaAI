"""Read models for the admin security dashboard (6.6): audit log, coverage, alerts, unlock.

All read-only except ``unlock``. Payloads in the audit log are metadata by design (ids,
counts, reason codes, hashes), so they are shown as recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.models import SecurityAuditLog, User
from spoldzielnia_ai.shield.coverage import SEVERITY

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from pydantic import JsonValue
    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.shield.coverage import CoverageTracker
    from spoldzielnia_ai.shield.risk import RiskEngine, RiskLevel, RiskStore

ALERT_EVENTS = (
    EventType.HONEYPOT_TRIGGERED,
    EventType.COVERAGE_LOCK,
    EventType.VERBATIM_BLOCKED,
    EventType.REFRESH_TOKEN_REUSE,
    EventType.SHIELD_DECISION,
    EventType.ACCOUNT_UNLOCKED,
)


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: list[SecurityAuditLog]
    next_cursor: int | None


async def audit_page(
    factory: SessionFactory,
    *,
    event_types: Sequence[str] = (),
    actor_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> AuditPage:
    """Newest first, keyset-paginated on ``id`` (stable while new rows arrive)."""
    query = select(SecurityAuditLog).order_by(SecurityAuditLog.id.desc()).limit(limit + 1)
    if event_types:
        query = query.where(SecurityAuditLog.event_type.in_(list(event_types)))
    if actor_id is not None:
        query = query.where(SecurityAuditLog.actor_id == actor_id)
    if since is not None:
        query = query.where(SecurityAuditLog.occurred_at >= since)
    if until is not None:
        query = query.where(SecurityAuditLog.occurred_at < until)
    if cursor is not None:
        query = query.where(SecurityAuditLog.id < cursor)
    async with factory() as session:
        rows = list(await session.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return AuditPage(rows, rows[-1].id if more and rows else None)


async def alerts(
    factory: SessionFactory, since: datetime | None, limit: int
) -> list[SecurityAuditLog]:
    """Incidents worth an admin's attention, newest first. Shield decisions only when they
    locked an account (CRITICAL); ELEVATED/HIGH ones stay in the audit log."""
    plain = [e.value for e in ALERT_EVENTS if e != EventType.SHIELD_DECISION]
    critical = and_(
        SecurityAuditLog.event_type == EventType.SHIELD_DECISION.value,
        SecurityAuditLog.payload["level"].astext == "CRITICAL",
    )
    query = (
        select(SecurityAuditLog)
        .where(or_(SecurityAuditLog.event_type.in_(plain), critical))
        .order_by(SecurityAuditLog.id.desc())
        .limit(limit)
    )
    if since is not None:
        query = query.where(SecurityAuditLog.occurred_at >= since)
    async with factory() as session:
        return list(await session.scalars(query))


@dataclass(frozen=True, slots=True)
class UserCoverage:
    user: User
    ratios: dict[str, float]
    blocks: dict[str, int]
    fraction_of_limit: float
    pressure: float
    incidents: float
    risk: float
    level: RiskLevel


async def coverage_overview(
    factory: SessionFactory,
    coverage: CoverageTracker,
    risk_store: RiskStore,
    engine: RiskEngine,
    settings: ShieldSettings,
    limit: int,
) -> list[UserCoverage]:
    """Every account's coverage and risk, riskiest first. The semantic score is per request
    and not stored; the risk shown is incidents + coverage pressure (what persists)."""
    async with factory() as session:
        users = list(await session.scalars(select(User).order_by(User.created_at)))
    result: list[UserCoverage] = []
    for user in users:
        status = await coverage.check(user.id)
        incidents = await risk_store.incidents(user.id)
        pressure = status.pressure(settings.min_blocks_before_lock)
        score = min(incidents + settings.risk_coverage_weight * pressure**2, 1.0)
        result.append(
            UserCoverage(
                user=user,
                ratios={w.value: round(status.usage[w].ratio, 4) for w in SEVERITY},
                blocks={w.value: status.usage[w].blocks for w in SEVERITY},
                fraction_of_limit=round(status.max_fraction_of_limit, 4),
                pressure=round(pressure, 4),
                incidents=round(incidents, 4),
                risk=round(score, 4),
                level=engine.level(score),
            )
        )
    result.sort(key=lambda u: (u.user.is_locked, u.risk), reverse=True)
    return result[:limit]


class NotLocked(Exception):
    pass


async def unlock(
    factory: SessionFactory,
    redis: Redis,
    risk_store: RiskStore,
    admin_id: UUID,
    user_id: UUID,
    reason: str,
    reset_risk: bool,
) -> User:
    """Unlock ``user_id`` (audited with the admin's reason), optionally clearing the shield's
    counters (incident risk, honeypot hits, open challenges) so it starts afresh."""
    async with factory() as session, session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise LookupError(str(user_id))
        if not user.is_locked:
            raise NotLocked(str(user_id))
        previous = user.locked_reason
        user.is_locked = False
        user.locked_reason = None
        user.locked_at = None
        if reset_risk:
            user.risk_score = 0.0
        payload: dict[str, JsonValue] = {
            "previous_reason": previous,
            "reason": reason[:500],
            "reset_risk": reset_risk,
        }
        await AuditService(session).record(
            EventType.ACCOUNT_UNLOCKED, admin_id, AuditTarget("app_user", str(user_id)), payload
        )
    if reset_risk:
        await redis.delete(
            f"risk:{user_id}", f"hp:{user_id}:hits", f"pow:{user_id}:open", f"pow:{user_id}:solved"
        )
        await risk_store.snapshot(user_id, 0.0)
    return user


async def record_access(
    factory: SessionFactory, admin_id: UUID, view: str, filters: dict[str, JsonValue]
) -> None:
    """Reading the security console is itself audited."""
    detail: dict[str, JsonValue] = {"view": view}
    detail.update({k: v for k, v in filters.items() if v is not None})
    async with factory() as session, session.begin():
        await AuditService(session).record(
            EventType.SECURITY_DASHBOARD_ACCESSED, admin_id, None, detail
        )
