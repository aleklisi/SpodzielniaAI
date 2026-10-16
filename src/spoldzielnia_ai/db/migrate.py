"""Programmatic Alembic: used by ``spoldzielnia migrate``, ``/readyz`` and the tests.

Migrations ship inside the package (``spoldzielnia_ai/db/migrations``), so the container needs
no ``alembic.ini``.
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

SCRIPT_LOCATION = "spoldzielnia_ai.db:migrations"


def alembic_config(url: str | None = None) -> Config:
    """Alembic config. ``url`` overrides the owner DSN that ``env.py`` loads from settings.

    The URL travels in ``attributes`` (not the ini options), so ``%`` in passwords is safe.
    """
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    if url is not None:
        config.attributes["url"] = url
    return config


def head_revision() -> str:
    """The single head revision shipped with this build."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no Alembic revisions found")
    return head


def upgrade(url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)
