"""Model metadata, enum <-> DB value mapping and agreement with the shipped migration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY

from spoldzielnia_ai.db.enums import (
    PG_ENUM_NAMES,
    ArticleStatus,
    ConsentStatus,
    ReviewDecision,
    Role,
    pg_enum,
)
from spoldzielnia_ai.db.migrate import alembic_config, head_revision
from spoldzielnia_ai.db.models import (
    ArticleBlock,
    Base,
    ExpertReview,
    PrivateNote,
    User,
    WikiArticle,
)

if TYPE_CHECKING:
    from enum import StrEnum
    from types import ModuleType

ENUMS: list[type[StrEnum]] = [Role, ConsentStatus, ArticleStatus, ReviewDecision]
DIALECT = postgresql.dialect()


def _table(model: type[Base]) -> Table:
    return cast("Table", model.__table__)


def test_tables_registered() -> None:
    assert set(Base.metadata.tables) == {
        "app_user",
        "private_note",
        "wiki_article",
        "article_block",
        "expert_review",
        "security_audit_log",
    }


@pytest.mark.parametrize("enum_cls", ENUMS)
def test_enum_db_values_are_member_values(enum_cls: type[StrEnum]) -> None:
    sa_type = pg_enum(enum_cls)
    assert sa_type.name == PG_ENUM_NAMES[enum_cls]
    assert sa_type.enums == [member.value for member in enum_cls]


Processor = Callable[[object], object]


def _processors(sa_type: SAEnum) -> tuple[Processor, Processor]:
    bind = cast("Processor | None", sa_type.bind_processor(DIALECT))
    result = cast("Processor | None", sa_type.result_processor(DIALECT, None))
    assert bind is not None
    assert result is not None
    return bind, result


@pytest.mark.parametrize("enum_cls", ENUMS)
def test_enum_round_trips_through_type_processors(enum_cls: type[StrEnum]) -> None:
    bind, result = _processors(pg_enum(enum_cls))
    for member in enum_cls:
        assert bind(member) == member.value
        assert result(member.value) is member


def test_enum_rejects_unknown_strings() -> None:
    bind, _ = _processors(pg_enum(ConsentStatus))
    with pytest.raises(LookupError):
        bind("DELETED")


@pytest.mark.parametrize(
    ("model", "column", "enum_cls"),
    [
        (WikiArticle, "status", ArticleStatus),
        (ArticleBlock, "consent_status", ConsentStatus),
        (ExpertReview, "decision", ReviewDecision),
    ],
)
def test_enum_columns_use_named_pg_types(
    model: type[Base], column: str, enum_cls: type[StrEnum]
) -> None:
    sa_type = _table(model).c[column].type
    assert isinstance(sa_type, SAEnum)
    assert sa_type.name == PG_ENUM_NAMES[enum_cls]


def test_user_roles_is_array_of_role_enum() -> None:
    sa_type = _table(User).c["roles"].type
    assert isinstance(sa_type, ARRAY)
    item = cast("ARRAY[Any]", sa_type).item_type
    assert isinstance(item, SAEnum)
    assert item.name == "user_role"


def test_block_provenance_columns_are_mandatory() -> None:
    """AGENTS.md §3.2: every block carries author_id and consent_status."""
    columns = _table(ArticleBlock).c
    assert columns["author_id"].nullable is False
    assert columns["consent_status"].nullable is False


def test_private_note_is_owned_and_stores_only_ciphertext() -> None:
    columns = _table(PrivateNote).c
    assert columns["owner_id"].nullable is False
    assert {"body_ciphertext", "body_nonce", "dek_wrapped"} <= set(columns.keys())
    assert "body" not in columns


def test_constraint_names_follow_convention() -> None:
    names = {c.name for c in _table(ArticleBlock).constraints}
    assert {
        "pk_article_block",
        "uq_article_block_article_id_position",
        "fk_article_block_author_id_app_user",
        "ck_article_block_position_non_negative",
    } <= names


def _revision_module(revision: str) -> ModuleType:
    script = ScriptDirectory.from_config(alembic_config()).get_revision(revision)
    assert script is not None
    return script.module


def test_single_head_is_core_schema() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0001_core_schema"]
    assert head_revision() == "0001_core_schema"


def test_migration_enum_types_match_python_enums() -> None:
    module = _revision_module("0001_core_schema")
    migrated = {e.name: list(e.enums) for e in cast("tuple[SAEnum, ...]", module.ENUMS)}
    assert migrated == {PG_ENUM_NAMES[e]: [m.value for m in e] for e in ENUMS}
