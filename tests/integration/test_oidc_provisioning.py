"""Chunk 1.5: just-in-time provisioning against Postgres, and the full callback with the real
provisioner (fake IdP)."""

from __future__ import annotations

import dataclasses
import secrets
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest
import respx
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.verify import verify_chain
from spoldzielnia_ai.auth.identity import OidcIdentity
from spoldzielnia_ai.auth.provisioning import DbUserProvisioner
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.models import User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.main import create_app
from tests.fixtures.oidc import ISSUER, FakeIdP

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_engine(migrated: PgInstance) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(app_engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(app_engine)


def _identity(subject: str, *roles: Role, email: str | None = "a@example.org") -> OidcIdentity:
    return OidcIdentity(
        issuer=ISSUER,
        subject=subject,
        email=email,
        display_name="Jan",
        roles=roles,
        acr="1",
        amr=("pwd",),
        auth_time=None,
    )


async def _events(factory: SessionFactory, user_id: UUID) -> list[tuple[str, dict[str, object]]]:
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT event_type, payload FROM security_audit_log "
                "WHERE actor_id = :id ORDER BY id"
            ),
            {"id": user_id},
        )
        return [(row.event_type, row.payload) for row in rows]


async def test_first_login_creates_user_then_mirrors_idp(
    factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    provisioner = DbUserProvisioner(factory)
    subject = secrets.token_hex(8)

    first = await provisioner.provision(_identity(subject, Role.MEMBER), "keycloak")
    assert first.created
    second = await provisioner.provision(
        _identity(subject, Role.MEMBER, Role.EXPERT, email=None), "keycloak"
    )
    assert not second.created
    assert second.id == first.id

    async with factory() as session:
        user = await session.scalar(select(User).where(User.id == first.id))
    assert user is not None
    assert (user.oidc_issuer, user.oidc_subject) == (ISSUER, subject)
    assert user.roles == [Role.MEMBER, Role.EXPERT]
    assert user.email is None
    assert user.updated_at >= user.created_at

    events = await _events(factory, first.id)
    assert [event for event, _ in events] == ["USER_PROVISIONED", "USER_LOGIN", "USER_LOGIN"]
    assert events[1][1] == {
        "provider": "keycloak",
        "outcome": "ok",
        "roles": ["MEMBER"],
        "acr": "1",
        "amr": ["pwd"],
    }
    assert (await verify_chain(app_engine)).ok


async def test_same_subject_from_another_issuer_is_another_user(factory: SessionFactory) -> None:
    provisioner = DbUserProvisioner(factory)
    subject = secrets.token_hex(8)
    a = await provisioner.provision(_identity(subject), "keycloak")
    other = dataclasses.replace(_identity(subject), issuer="https://other.example")
    b = await provisioner.provision(other, "keycloak")
    assert a.id != b.id
    assert b.created


async def test_login_never_unlocks_a_locked_user(factory: SessionFactory) -> None:
    provisioner = DbUserProvisioner(factory)
    subject = secrets.token_hex(8)
    user = await provisioner.provision(_identity(subject), "keycloak")
    async with factory() as session, session.begin():
        await session.execute(update(User).where(User.id == user.id).values(is_locked=True))

    again = await provisioner.provision(_identity(subject, Role.ADMIN), "keycloak")
    assert again.is_locked
    events = await _events(factory, user.id)
    assert events[-1][0] == "USER_LOGIN"
    assert events[-1][1]["outcome"] == "locked"


async def test_full_callback_provisions_user(
    settings: Settings, factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    app = create_app(settings, readiness_checks={})
    app.state.redis = FakeAsyncRedis()
    app.state.session_factory = factory
    idp = FakeIdP()
    subject = secrets.token_hex(8)

    with respx.mock(assert_all_called=False) as router:
        idp.mount(router)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="https://spoldzielnia.localhost"
        ) as client:
            login = await client.get("/auth/login", params={"return_to": "/wiki"})
            params = parse_qs(urlsplit(login.headers["location"]).query)
            idp.id_token = idp.make_id_token(
                nonce=params["nonce"][0], sub=subject, roles=["EXPERT", "MEMBER"]
            )
            response = await client.get(
                "/auth/callback", params={"state": params["state"][0], "code": "c"}
            )

    assert response.status_code == 303
    assert response.headers["location"] == "/wiki"
    async with factory() as session:
        user = await session.scalar(select(User).where(User.oidc_subject == subject))
    assert user is not None
    assert user.roles == [Role.EXPERT, Role.MEMBER]
    assert user.email == "pszczelarz@example.org"
    assert (await verify_chain(app_engine)).ok
