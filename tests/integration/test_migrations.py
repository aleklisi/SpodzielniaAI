"""Chunk 1.3: migrations round-trip, models match the schema, and the runtime role is confined.

Runs against a throwaway Postgres (testcontainers) bootstrapped by infra/postgres/init.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from spoldzielnia_ai.core.readiness import migrations_check
from spoldzielnia_ai.db.enums import ConsentStatus, Role
from spoldzielnia_ai.db.migrate import downgrade, head_revision, upgrade
from spoldzielnia_ai.db.models import ArticleBlock, Base, User, WikiArticle

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

APP_TABLES = {
    "app_user",
    "private_note",
    "wiki_article",
    "article_block",
    "expert_review",
    "security_audit_log",
}
ENUM_TYPES = {"user_role", "consent_status", "article_status", "review_decision"}
INSUFFICIENT_PRIVILEGE = "42501"


async def _fetch(url: str, sql: str) -> list[Any]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql))).scalars())
    finally:
        await engine.dispose()


def _tables(pg: PgInstance) -> set[str]:
    sql = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    return set(asyncio.run(_fetch(pg.owner_url, sql)))


def _enum_types(pg: PgInstance) -> set[str]:
    sql = "SELECT typname FROM pg_type WHERE typtype = 'e'"
    return set(asyncio.run(_fetch(pg.owner_url, sql)))


def test_upgrade_downgrade_round_trip(pg: PgInstance) -> None:
    upgrade(pg.owner_url)
    assert _tables(pg) >= APP_TABLES
    assert _enum_types(pg) >= ENUM_TYPES

    downgrade(pg.owner_url, "base")
    assert not _tables(pg) & APP_TABLES
    assert not _enum_types(pg) & ENUM_TYPES

    upgrade(pg.owner_url)
    sql = "SELECT version_num FROM alembic_version"
    assert asyncio.run(_fetch(pg.owner_url, sql)) == [head_revision()]


def test_models_match_migrated_schema(migrated: PgInstance) -> None:
    """Autogenerate finds nothing to do: models and migrations agree."""

    def diff(conn: Connection) -> list[Any]:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async def run() -> list[Any]:
        engine = create_async_engine(migrated.owner_url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(diff)
        finally:
            await engine.dispose()

    assert asyncio.run(run()) == []


async def test_app_role_attributes(migrated: PgInstance) -> None:
    rows = await _fetch(
        migrated.superuser_url.replace("/spoldzielnia", "/postgres"),
        """
        SELECT row(rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit)::text
        FROM pg_roles WHERE rolname = 'spoldzielnia_app'
        """,
    )
    assert rows == ["(f,f,f,f,f)"]
    member = await _fetch(
        migrated.owner_url, "SELECT pg_has_role('spoldzielnia_app', 'spoldzielnia_owner', 'MEMBER')"
    )
    assert member == [False]


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE evil (id int)",
        "CREATE SCHEMA evil",
        "ALTER TABLE app_user ADD COLUMN evil int",
        "ALTER TABLE private_note DISABLE ROW LEVEL SECURITY",
        "DROP TABLE private_note",
        "TRUNCATE app_user",
        "CREATE INDEX evil ON app_user (email)",
        "SET ROLE spoldzielnia_owner",
        "UPDATE alembic_version SET version_num = 'evil'",
        "DELETE FROM alembic_version",
    ],
)
async def test_app_role_cannot_change_schema(migrated: PgInstance, statement: str) -> None:
    engine = create_async_engine(migrated.app_url)
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as excinfo:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()
    assert getattr(excinfo.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE


async def test_app_role_has_dml_and_enums_round_trip(migrated: PgInstance) -> None:
    engine = create_async_engine(migrated.app_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            user = User(
                oidc_issuer="https://idp.test", oidc_subject="s-1", roles=[Role.MEMBER, Role.EXPERT]
            )
            session.add(user)
            await session.flush()
            article = WikiArticle(title="Warroza")
            session.add(article)
            await session.flush()
            block = ArticleBlock(
                article_id=article.id,
                position=0,
                author_id=user.id,
                content="Kwas szczawiowy…",
                content_hash="0" * 64,
            )
            session.add(block)
            await session.flush()

            loaded = await session.scalar(select(User).where(User.id == user.id))
            assert loaded is not None
            assert loaded.roles == [Role.MEMBER, Role.EXPERT]
            assert loaded.id.version == 7
            assert loaded.created_at.tzinfo is not None
            assert (
                await session.scalar(
                    select(ArticleBlock.consent_status).where(ArticleBlock.id == block.id)
                )
                is ConsentStatus.ACTIVE
            )
            assert article.version == 1
            await session.rollback()
    finally:
        await engine.dispose()


async def test_readiness_reports_schema_head(migrated: PgInstance) -> None:
    engine = create_async_engine(migrated.app_url)
    try:
        assert await migrations_check(engine, head_revision())() is True
        assert await migrations_check(engine, "9999_future")() is False
    finally:
        await engine.dispose()
