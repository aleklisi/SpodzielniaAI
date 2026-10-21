"""In-memory stand-in for the Keycloak Admin API (``UserIdp``), for user-management tests."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from spoldzielnia_ai.auth.keycloak_admin import (
    ActionEmailFailed,
    KcUser,
    KeycloakAdminError,
    PasswordRejected,
    UserExists,
    UserNotFound,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

REALM_ROLES = {"MEMBER", "EXPERT", "ADMIN", "default-roles-spoldzielnia"}


@dataclass
class FakeUserIdp:
    configured: bool = True
    users: dict[str, KcUser] = field(default_factory=dict[str, KcUser])
    roles: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    passwords: dict[str, str] = field(default_factory=dict[str, str])
    emails_sent: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, tuple[str, ...]]]
    )
    logouts: list[str] = field(default_factory=list[str])
    calls: list[str] = field(default_factory=list[str])
    # Failure injection: the name of a method that raises ``fail_with``.
    fail_on: str | None = None
    fail_with: type[Exception] = KeycloakAdminError
    min_password_length: int = 12
    # Seconds each role read takes: lets concurrent operations really overlap.
    delay: float = 0.0

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name:
            raise self.fail_with(f"injected failure in {name}")

    def _get(self, user_id: str) -> KcUser:
        try:
            return self.users[user_id]
        except KeyError:
            raise UserNotFound(user_id) from None

    def add(self, username: str, *roles: str, **attrs: object) -> KcUser:
        """Seed a user as if created in the Keycloak console."""
        user = KcUser(
            id=str(uuid.uuid4()),
            username=username,
            email=f"{username}@example.test",
            first_name=username.title(),
            last_name=None,
            enabled=True,
            email_verified=True,
            required_actions=(),
            totp=False,
        )
        user = dataclasses.replace(user, **attrs)  # pyright: ignore[reportArgumentType]
        self.users[user.id] = user
        self.roles[user.id] = {"default-roles-spoldzielnia", *roles}
        return user

    async def search_users(
        self, search: str = "", first: int = 0, maximum: int = 100
    ) -> list[KcUser]:
        self._maybe_fail("search_users")
        found = [u for u in self.users.values() if search.lower() in u.username]
        return found[first : first + maximum]

    async def get_user(self, user_id: str) -> KcUser:
        self._maybe_fail("get_user")
        return self._get(user_id)

    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
        required_actions: Sequence[str],
    ) -> str:
        self._maybe_fail("create_user")
        if any(u.username == username for u in self.users.values()):
            raise UserExists(username)
        user = self.add(
            username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            email_verified=False,
            required_actions=tuple(required_actions),
        )
        return user.id

    async def update_user(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        required_actions: Sequence[str] | None = None,
    ) -> None:
        self._maybe_fail("update_user")
        user = self._get(user_id)
        if enabled is not None:
            user = dataclasses.replace(user, enabled=enabled)
        if required_actions is not None:
            user = dataclasses.replace(user, required_actions=tuple(required_actions))
        self.users[user_id] = user

    async def delete_user(self, user_id: str) -> None:
        self._maybe_fail("delete_user")
        self.users.pop(user_id, None)
        self.roles.pop(user_id, None)

    async def realm_roles_of(self, user_id: str) -> set[str]:
        self._maybe_fail("realm_roles_of")
        if self.delay:
            await asyncio.sleep(self.delay)
        self._get(user_id)
        return set(self.roles[user_id])

    async def add_realm_roles(self, user_id: str, names: Sequence[str]) -> None:
        self._maybe_fail("add_realm_roles")
        self._get(user_id)
        unknown = set(names) - REALM_ROLES
        if unknown:
            raise KeycloakAdminError(f"realm roles not available: {sorted(unknown)}")
        self.roles[user_id] |= set(names)

    async def remove_realm_roles(self, user_id: str, names: Sequence[str]) -> None:
        self._maybe_fail("remove_realm_roles")
        self._get(user_id)
        self.roles[user_id] -= set(names)

    async def set_temporary_password(self, user_id: str, password: str) -> None:
        self._maybe_fail("set_temporary_password")
        user = self._get(user_id)
        if len(password) < self.min_password_length:
            raise PasswordRejected("invalidPasswordMinLengthMessage")
        self.passwords[user_id] = password
        actions = tuple(sorted({*user.required_actions, "UPDATE_PASSWORD"}))
        self.users[user_id] = dataclasses.replace(user, required_actions=actions)

    async def send_actions_email(self, user_id: str, actions: Sequence[str]) -> None:
        if self.fail_on == "send_actions_email":
            self.calls.append("send_actions_email")
            raise ActionEmailFailed("no smtp")
        self._maybe_fail("send_actions_email")
        user = self._get(user_id)
        if not user.email:
            raise ActionEmailFailed("no email")
        self.emails_sent.append((user_id, tuple(actions)))

    async def logout(self, user_id: str) -> None:
        self._maybe_fail("logout")
        self._get(user_id)
        self.logouts.append(user_id)
