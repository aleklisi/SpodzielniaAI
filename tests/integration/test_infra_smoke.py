"""Chunk 1.2a: every infrastructure service is up, authenticated and reachable via the
dev debug ports (127.0.0.1 only)."""

from __future__ import annotations

import json
import socket
import ssl
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import AuthenticationError, ResponseError
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.stack import SECRETS_DIR, compose, compose_files, require_port, secret

pytestmark = pytest.mark.integration


def _pg_url(user: str, password: str, database: str = "spoldzielnia") -> URL:
    return URL.create(
        "postgresql+asyncpg",
        username=user,
        password=password,
        host="127.0.0.1",
        port=5432,
        database=database,
    )


async def test_postgres_app_role_is_unprivileged() -> None:
    require_port(5432)
    engine = create_async_engine(_pg_url("spoldzielnia_app", secret("pg_app_password")))
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            can_create = (
                await conn.execute(text("SELECT has_schema_privilege('public', 'CREATE')"))
            ).scalar_one()
    finally:
        await engine.dispose()

    assert tuple(row) == (False, False, False, False)
    assert can_create is False


async def test_postgres_owner_owns_schema() -> None:
    require_port(5432)
    engine = create_async_engine(_pg_url("spoldzielnia_owner", secret("pg_owner_password")))
    try:
        async with engine.connect() as conn:
            owner = (
                await conn.execute(
                    text("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='public'")
                )
            ).scalar_one()
            bypass = (
                await conn.execute(
                    text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert owner == "spoldzielnia_owner"
    assert bypass is False


async def test_redis_requires_password() -> None:
    require_port(6379)
    anonymous = Redis(host="127.0.0.1", port=6379)
    try:
        with pytest.raises((AuthenticationError, ResponseError)):
            await anonymous.set("probe", "1")  # pyright: ignore[reportUnknownMemberType]
    finally:
        await anonymous.aclose()

    client = Redis(host="127.0.0.1", port=6379, password=secret("redis_password"))
    try:
        assert await client.ping() is True  # pyright: ignore[reportUnknownMemberType]
    finally:
        await client.aclose()


async def test_qdrant_tls_and_api_key() -> None:
    require_port(6333)
    ca = ssl.create_default_context(cafile=SECRETS_DIR / "tls" / "ca.pem")
    async with httpx.AsyncClient(base_url="https://127.0.0.1:6333", verify=ca) as client:
        anonymous = await client.get("/collections")
        authorized = await client.get("/collections", headers={"api-key": secret("qdrant_api_key")})

    assert anonymous.status_code == 401
    assert authorized.status_code == 200


async def test_qdrant_rejects_plain_http() -> None:
    require_port(6333)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:6333") as client:
        with pytest.raises(httpx.HTTPError):
            await client.get("/collections")


async def test_minio_is_live() -> None:
    require_port(9000)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:9000") as client:
        response = await client.get("/minio/health/live")
    assert response.status_code == 200


def test_clamav_answers_ping() -> None:
    require_port(3310)
    with socket.create_connection(("127.0.0.1", 3310), timeout=5) as sock:
        sock.sendall(b"zPING\0")
        assert sock.recv(16).rstrip(b"\0") == b"PONG"


def test_compose_config_has_no_plaintext_secrets() -> None:
    result = compose("config")
    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    for path in SECRETS_DIR.iterdir():
        if path.is_file():
            value = path.read_text().strip()
            if not value:  # optional secrets (e.g. llm_api_key) may be empty
                continue
            assert value not in rendered, f"secret {path.name} leaked into compose config"


def _published_ports(*files: str) -> dict[str, list[dict[str, object]]]:
    result = compose_files(files, "config", "--format", "json")
    assert result.returncode == 0, result.stderr
    services: dict[str, dict[str, Any]] = json.loads(result.stdout)["services"]
    return {name: list(s.get("ports", [])) for name, s in services.items() if s.get("ports")}


def test_dev_ports_bound_to_loopback_only() -> None:
    for name, ports in _published_ports("compose.yaml", "compose.dev.yaml").items():
        for port in ports:
            assert port.get("host_ip") == "127.0.0.1", f"{name} publishes {port} beyond loopback"


def test_base_compose_publishes_only_proxy() -> None:
    published = _published_ports("compose.yaml")
    assert set(published) == {"proxy"}
    assert sorted(str(p["published"]) for p in published["proxy"]) == ["443", "80"]
