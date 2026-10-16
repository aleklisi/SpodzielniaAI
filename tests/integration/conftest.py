"""Fixtures for integration tests that need their own throwaway services (testcontainers)."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.engine import URL

from tests.integration.stack import REPO_ROOT, compose_image, require_docker

if TYPE_CHECKING:
    from collections.abc import Iterator

POSTGRES_INIT_DIR = REPO_ROOT / "infra" / "postgres" / "init"
PG_SECRET_NAMES = ("pg_owner_password", "pg_app_password", "pg_keycloak_password")


@dataclass(frozen=True)
class PgInstance:
    host: str
    port: int
    passwords: dict[str, str]

    def url(self, user: str, password_secret: str, database: str = "spoldzielnia") -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=user,
            password=self.passwords[password_secret],
            host=self.host,
            port=self.port,
            database=database,
        ).render_as_string(hide_password=False)

    @property
    def owner_url(self) -> str:
        return self.url("spoldzielnia_owner", "pg_owner_password")

    @property
    def app_url(self) -> str:
        return self.url("spoldzielnia_app", "pg_app_password")

    @property
    def superuser_url(self) -> str:
        return self.url("postgres", "superuser")


def _wait_for_owner_login(pg: PgInstance, timeout: float = 60.0) -> None:
    """The init scripts run before Postgres listens on TCP; the owner role exists only after."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def probe() -> None:
        engine = create_async_engine(pg.owner_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    deadline = time.monotonic() + timeout
    while True:
        try:
            asyncio.run(probe())
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def pg(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PgInstance]:
    """Postgres 16 (compose image) initialised by the real infra/postgres/init scripts."""
    require_docker()
    from testcontainers.community.postgres import PostgresContainer

    passwords = {name: secrets.token_urlsafe(24) for name in (*PG_SECRET_NAMES, "superuser")}
    secrets_dir = tmp_path_factory.mktemp("pg-secrets")
    for name in PG_SECRET_NAMES:
        (secrets_dir / name).write_text(passwords[name])
        (secrets_dir / name).chmod(0o644)  # read by the postgres uid in the container
    secrets_dir.chmod(0o755)

    container = (
        PostgresContainer(
            compose_image("postgres"),
            username="postgres",
            password=passwords["superuser"],
            dbname="postgres",
            driver=None,
        )
        .with_volume_mapping(str(POSTGRES_INIT_DIR), "/docker-entrypoint-initdb.d", "ro")
        .with_volume_mapping(str(secrets_dir), "/run/secrets", "ro")
    )
    with container:
        instance = PgInstance(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            passwords=passwords,
        )
        _wait_for_owner_login(instance)
        yield instance
