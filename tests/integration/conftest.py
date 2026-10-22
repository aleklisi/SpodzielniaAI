"""Fixtures for integration tests that need their own throwaway services (testcontainers)."""

from __future__ import annotations

import secrets
import time
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.engine import URL

from tests.integration.stack import REPO_ROOT, compose_image, require_docker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from qdrant_client import AsyncQdrantClient

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


@dataclass(frozen=True)
class QdrantInstance:
    url: str
    api_key: str


@pytest.fixture(scope="session")
def qdrant_server() -> Iterator[QdrantInstance]:
    """Qdrant (compose image) over plain HTTP with an API key. The TLS setup is covered by the
    stack smoke test; here only the data plane matters."""
    require_docker()
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import HttpWaitStrategy

    api_key = secrets.token_urlsafe(24)
    container = (
        DockerContainer(compose_image("qdrant/qdrant"))
        .with_env("QDRANT__SERVICE__API_KEY", api_key)
        .with_env("QDRANT__TELEMETRY_DISABLED", "true")
        .with_exposed_ports(6333)
        .waiting_for(HttpWaitStrategy(6333, "/readyz").for_status_code(200))
    )
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(6333))
        yield QdrantInstance(f"http://{host}:{port}", api_key)


@pytest.fixture
async def qdrant(qdrant_server: QdrantInstance) -> AsyncIterator[AsyncQdrantClient]:
    """A client on freshly recreated collections (vector size ``EMBED_DIM``)."""
    from pydantic import SecretStr

    from spoldzielnia_ai.core.settings import QdrantSettings
    from spoldzielnia_ai.vector.client import make_qdrant
    from spoldzielnia_ai.vector.collections import recreate_collections
    from tests.fixtures.llm import EMBED_DIM

    settings = QdrantSettings(url=qdrant_server.url, api_key=SecretStr(qdrant_server.api_key))
    with warnings.catch_warnings():  # "Api key is used with an insecure connection"
        warnings.simplefilter("ignore", UserWarning)
        client = make_qdrant(settings)
    # Aliases and collections of index generations (10.3) left by earlier tests: an alias
    # holding a family name would otherwise redirect the bare test collections.
    from qdrant_client import models

    for alias in (await client.get_aliases()).aliases:
        await client.update_collection_aliases(
            change_aliases_operations=[
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=alias.alias_name)
                )
            ]
        )
    for collection in (await client.get_collections()).collections:
        if "__g" in collection.name:
            await client.delete_collection(collection.name)
    await recreate_collections(client, EMBED_DIM)
    yield client
    await client.close()


@pytest.fixture
def migrated(pg: PgInstance) -> PgInstance:
    """``pg`` upgraded to the head revision."""
    from spoldzielnia_ai.db.migrate import upgrade

    upgrade(pg.owner_url)
    return pg


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """Real Redis (compose image): HyperLogLog accuracy can't be tested on fakeredis."""
    require_docker()
    from testcontainers.community.redis import RedisContainer

    with RedisContainer(compose_image("redis")) as container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(6379))
        yield f"redis://{host}:{port}/0"


# The schema setup infra/postgres/init/10-roles.sh applies to `spoldzielnia`.
_DB_SETUP = (
    "ALTER SCHEMA public OWNER TO spoldzielnia_owner",
    "REVOKE ALL ON SCHEMA public FROM PUBLIC",
    "GRANT USAGE ON SCHEMA public TO spoldzielnia_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE spoldzielnia_owner IN SCHEMA public "
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO spoldzielnia_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE spoldzielnia_owner IN SCHEMA public "
    "GRANT USAGE, SELECT ON SEQUENCES TO spoldzielnia_app",
    "ALTER DEFAULT PRIVILEGES FOR ROLE spoldzielnia_owner IN SCHEMA public "
    "GRANT EXECUTE ON FUNCTIONS TO spoldzielnia_app",
)


@pytest.fixture
async def fresh_db(pg: PgInstance) -> AsyncIterator[str]:
    """A new, migrated database with the production role setup, dropped afterwards. For tests
    whose assertions depend on everything in the database (e.g. corpus-wide ratios). Yields
    the runtime (app role) URL."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from spoldzielnia_ai.db.migrate import upgrade

    name = f"t_{secrets.token_hex(6)}"
    autocommit = {"isolation_level": "AUTOCOMMIT"}
    admin = create_async_engine(pg.url("postgres", "superuser", "postgres"), **autocommit)
    async with admin.connect() as conn:
        for statement in (
            f'CREATE DATABASE "{name}" OWNER spoldzielnia_owner',
            f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC',
            f'GRANT CONNECT, TEMPORARY ON DATABASE "{name}" TO spoldzielnia_app',
        ):
            await conn.execute(text(statement))
    setup = create_async_engine(pg.url("postgres", "superuser", name), **autocommit)
    async with setup.connect() as conn:
        for statement in _DB_SETUP:
            await conn.execute(text(statement))
    await setup.dispose()
    await asyncio.to_thread(upgrade, pg.url("spoldzielnia_owner", "pg_owner_password", name))
    try:
        yield pg.url("spoldzielnia_app", "pg_app_password", name)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
