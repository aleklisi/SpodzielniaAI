"""Chunk 6.6 backend: the admin security console (ADMIN + MFA, read-only, audited)."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService
from spoldzielnia_ai.db.models import SecurityAuditLog, User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.shield.alerts import LogNotifier
from spoldzielnia_ai.shield.coverage import CorpusCounter, CoverageTracker
from spoldzielnia_ai.shield.locks import AccountLocker
from tests.fixtures.app import harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import JsonValue

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

READS = ["/admin/audit-log", "/admin/audit-log/verify", "/admin/coverage", "/admin/alerts"]


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(
    settings: Settings, factory: SessionFactory, migrated: PgInstance
) -> AsyncIterator[Harness]:
    async with harness(settings, factory) as h:
        h.app.state.engine = create_async_engine(migrated.app_url)  # the chain verifier's
        yield h
        await h.app.state.engine.dispose()


async def _admin(h: Harness) -> LoggedIn:
    return await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"])


async def _record(
    factory: SessionFactory, event: EventType, actor: UUID, **payload: JsonValue
) -> None:
    async with factory() as session, session.begin():
        await AuditService(session).record(event, actor, None, payload)


async def test_authorization_matrix(h: Harness) -> None:
    member = await h.login()
    expert = await h.login(roles=["EXPERT", "MEMBER"], amr=["pwd", "otp"])
    admin_without_mfa = await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd"])
    admin = await _admin(h)
    target = member.user_id
    for user, expected in ((member, 403), (expert, 403), (admin_without_mfa, 403), (admin, 200)):
        for path in READS:
            response = await user.client.get(path)
            assert response.status_code == expected, (path, response.text)
        unlock = await user.client.post(
            f"/admin/users/{target}/unlock",
            json={"reason": "Sprawdzone: fałszywy alarm."},
            headers=user.headers,
        )
        assert unlock.status_code == (409 if expected == 200 else expected)  # member isn't locked


async def test_audit_log_filters_paginates_and_audits_access(
    h: Harness, factory: SessionFactory
) -> None:
    admin = await _admin(h)
    subject = UUID((await h.login()).user_id)
    for i in range(5):
        await _record(factory, EventType.VERBATIM_BLOCKED, subject, reason="ngram_overlap", i=i)
    await _record(factory, EventType.HONEYPOT_TRIGGERED, subject, triggers=1)

    first = (
        await admin.client.get(
            "/admin/audit-log",
            params={"actor": str(subject), "event_type": "VERBATIM_BLOCKED", "limit": 3},
        )
    ).json()
    second = (
        await admin.client.get(
            "/admin/audit-log",
            params={
                "actor": str(subject),
                "event_type": "VERBATIM_BLOCKED",
                "limit": 3,
                "cursor": first["next_cursor"],
            },
        )
    ).json()
    both = (
        await admin.client.get(
            "/admin/audit-log",
            params=[
                ("actor", str(subject)),
                ("event_type", "VERBATIM_BLOCKED"),
                ("event_type", "HONEYPOT_TRIGGERED"),
            ],
        )
    ).json()

    assert [e["payload"]["i"] for e in first["items"]] == [4, 3, 2]
    assert [e["payload"]["i"] for e in second["items"]] == [1, 0]
    assert second["next_cursor"] is None
    assert len(both["items"]) == 6
    async with factory() as session:
        accessed = list(
            await session.scalars(
                select(SecurityAuditLog).where(
                    SecurityAuditLog.event_type == EventType.SECURITY_DASHBOARD_ACCESSED.value,
                    SecurityAuditLog.actor_id == UUID(admin.user_id),
                )
            )
        )
    assert len(accessed) == 3
    assert accessed[0].payload["view"] == "audit_log"


async def test_chain_verifies(h: Harness) -> None:
    admin = await _admin(h)
    chain = (await admin.client.get("/admin/audit-log/verify")).json()
    assert chain["ok"] is True
    assert chain["breaks"] == []
    assert chain["rows"] >= 1
    assert len(chain["head_hash"]) == 64


async def test_alerts_keep_only_what_needs_attention(h: Harness, factory: SessionFactory) -> None:
    admin = await _admin(h)
    subject = UUID((await h.login()).user_id)
    await _record(factory, EventType.SHIELD_DECISION, subject, level="ELEVATED", marker="elevated")
    await _record(factory, EventType.SHIELD_DECISION, subject, level="CRITICAL", marker="critical")
    await _record(factory, EventType.COVERAGE_LOCK, subject, window="7d", marker="lock")
    await _record(factory, EventType.NOTE_PROMOTED, subject, marker="noise")

    alerts = (await admin.client.get("/admin/alerts")).json()
    mine = [a["payload"]["marker"] for a in alerts if a["actor_id"] == str(subject)]
    assert mine == ["lock", "critical"]


async def test_coverage_lists_locked_and_riskiest_accounts_first(
    h: Harness, factory: SessionFactory
) -> None:
    admin = await _admin(h)
    quiet, busy = UUID((await h.login()).user_id), UUID((await h.login()).user_id)
    redis = h.app.state.redis
    tracker = CoverageTracker(redis, CorpusCounter(redis, factory, 60), h.app.state.settings.shield)
    await tracker.record(busy, [uuid4() for _ in range(40)], [uuid4()])
    await AccountLocker(factory, LogNotifier()).lock(quiet, "honeypot", EventType.SHIELD_DECISION)

    users = (await admin.client.get("/admin/coverage")).json()["users"]
    by_id = {u["user_id"]: u for u in users}
    assert by_id[str(busy)]["blocks"]["7d"] == 40
    assert by_id[str(busy)]["pressure"] > 0
    assert by_id[str(quiet)]["is_locked"] is True
    assert by_id[str(quiet)]["locked_reason"] == "honeypot"
    order = [u["user_id"] for u in users]
    assert order.index(str(quiet)) < order.index(str(busy))  # locked first


async def test_unlock_restores_the_account(h: Harness, factory: SessionFactory) -> None:
    admin = await _admin(h)
    subject = f"unlock-{secrets.token_hex(4)}"
    member = await h.login(subject)
    member_id = UUID(member.user_id)
    await h.app.state.redis.hset(f"risk:{member_id}", mapping={"v": 0.9, "t": 0})
    await AccountLocker(factory, LogNotifier()).lock(
        member_id, "risk_critical", EventType.SHIELD_DECISION
    )
    assert (await member.client.get("/me")).status_code == 423

    short = await admin.client.post(
        f"/admin/users/{member_id}/unlock", json={"reason": "ok"}, headers=admin.headers
    )
    done = await admin.client.post(
        f"/admin/users/{member_id}/unlock",
        json={"reason": "Wyjaśnione z członkiem: skrypt testowy."},
        headers=admin.headers,
    )
    again = await admin.client.post(
        f"/admin/users/{member_id}/unlock",
        json={"reason": "Wyjaśnione z członkiem: skrypt testowy."},
        headers=admin.headers,
    )

    assert short.status_code == 422
    assert done.json() == {"user_id": str(member_id), "is_locked": False}
    assert again.status_code == 409
    assert await h.app.state.redis.exists(f"risk:{member_id}") == 0
    relogged = await h.login(subject)  # the lock dropped the old sessions; a new one works
    me = await relogged.client.get("/me")
    assert me.status_code == 200
    assert me.json()["id"] == str(member_id)
    async with factory() as session:
        user = await session.get(User, member_id)
        entry = await session.scalar(
            select(SecurityAuditLog).where(
                SecurityAuditLog.event_type == EventType.ACCOUNT_UNLOCKED.value,
                SecurityAuditLog.target_id == str(member_id),
            )
        )
    assert user is not None
    assert (user.is_locked, user.locked_reason, user.risk_score) == (False, None, 0.0)
    assert entry is not None
    assert entry.actor_id == UUID(admin.user_id)
    assert entry.payload["previous_reason"] == "risk_critical"
