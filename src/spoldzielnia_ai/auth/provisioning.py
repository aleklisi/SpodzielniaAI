"""Just-in-time user provisioning on login, keyed by ``(issuer, subject)``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import Boolean, func, literal_column
from sqlalchemy.dialects.postgresql import insert

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.db.models import User

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.auth.identity import OidcIdentity
    from spoldzielnia_ai.db.enums import Role
    from spoldzielnia_ai.db.session import SessionFactory


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    id: UUID
    roles: tuple[Role, ...]
    is_locked: bool
    created: bool


class UserProvisioner(Protocol):
    async def provision(self, identity: OidcIdentity, provider: str) -> ProvisionedUser: ...


class DbUserProvisioner:
    """Upserts the user and audits the login in one transaction.

    Profile and roles mirror the IdP on every login. ``is_locked`` and ``risk_score`` belong to
    the application and are never touched here. A locked user is still upserted, but the login
    is audited with ``outcome="locked"``, and the caller refuses it.
    """

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    async def provision(self, identity: OidcIdentity, provider: str) -> ProvisionedUser:
        roles = list(identity.roles)
        stmt = insert(User).values(
            id=uuid7(),
            oidc_issuer=identity.issuer,
            oidc_subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            roles=roles,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.oidc_issuer, User.oidc_subject],
            set_={
                "email": stmt.excluded.email,
                "display_name": stmt.excluded.display_name,
                "roles": stmt.excluded.roles,
                "updated_at": func.now(),
            },
        ).returning(
            User.id,
            User.is_locked,
            # xmax is 0 only for a freshly inserted row version.
            literal_column("(xmax = 0)", Boolean).label("created"),
        )

        async with self._factory() as session, session.begin():
            row = (await session.execute(stmt)).one()
            user = ProvisionedUser(
                id=row.id, roles=identity.roles, is_locked=row.is_locked, created=row.created
            )
            audit = AuditService(session)
            target = AuditTarget("app_user", str(user.id))
            if user.created:
                await audit.record(
                    EventType.USER_PROVISIONED,
                    user.id,
                    target,
                    {"provider": provider, "roles": [r.value for r in identity.roles]},
                )
            await audit.record(
                EventType.USER_LOGIN,
                user.id,
                target,
                {
                    "provider": provider,
                    "outcome": "locked" if user.is_locked else "ok",
                    "roles": [r.value for r in identity.roles],
                    "acr": identity.acr,
                    "amr": list(identity.amr),
                },
            )
        return user
