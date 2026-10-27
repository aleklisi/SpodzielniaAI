"""Just-in-time user provisioning on login, keyed by ``(issuer, subject)``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import Boolean, func, literal_column, or_
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
    locale: str | None = None  # the account's UI language after this login (14.1)


class UserProvisioner(Protocol):
    async def provision(
        self, identity: OidcIdentity, provider: str, locale: str | None = None
    ) -> ProvisionedUser: ...


class DbUserProvisioner:
    """Upserts the user and audits the login in one transaction.

    Profile and roles mirror the IdP on every login. ``is_locked``, the admin block and
    ``risk_score`` belong to the application and are never touched here. A locked or blocked user
    is still upserted, but the login is audited with ``outcome="locked"``, and the caller
    refuses it. An account an admin created (8.3) is a row with the same ``(issuer, subject)``,
    adopted here without a special case. ``locale`` is a signed-out visitor's choice (the
    ``lang`` cookie): it is saved only if the account has none yet (14.1).
    """

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    async def provision(
        self, identity: OidcIdentity, provider: str, locale: str | None = None
    ) -> ProvisionedUser:
        roles = list(identity.roles)
        stmt = insert(User).values(
            id=uuid7(),
            oidc_issuer=identity.issuer,
            oidc_subject=identity.subject,
            email=identity.email,
            display_name=identity.display_name,
            username=identity.username,
            roles=roles,
            last_login_at=func.now(),
            locale=locale,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.oidc_issuer, User.oidc_subject],
            set_={
                "email": stmt.excluded.email,
                "display_name": stmt.excluded.display_name,
                "username": stmt.excluded.username,
                "roles": stmt.excluded.roles,
                "last_login_at": func.now(),
                # Keycloak issues tokens only after every required action is done.
                "required_actions": [],
                "locale": func.coalesce(User.locale, stmt.excluded.locale),
                "updated_at": func.now(),
            },
        ).returning(
            User.id,
            User.locale,
            or_(User.is_locked, User.blocked_at.is_not(None)).label("is_locked"),
            # xmax is 0 only for a freshly inserted row version.
            literal_column("(xmax = 0)", Boolean).label("created"),
        )

        async with self._factory() as session, session.begin():
            row = (await session.execute(stmt)).one()
            user = ProvisionedUser(
                id=row.id,
                roles=identity.roles,
                is_locked=row.is_locked,
                created=row.created,
                locale=row.locale,
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
