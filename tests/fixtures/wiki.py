"""Seeding wiki articles and blocks directly in Postgres (Stage 4 tests)."""

from __future__ import annotations

import hashlib
import secrets
from typing import TYPE_CHECKING

from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, Role
from spoldzielnia_ai.db.models import ArticleBlock, User, WikiArticle

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.db.session import SessionFactory


async def make_user(factory: SessionFactory, *roles: Role) -> UUID:
    async with factory() as session, session.begin():
        user = User(
            oidc_issuer="https://idp.test",
            oidc_subject=secrets.token_hex(8),
            roles=list(roles) or [Role.MEMBER],
        )
        session.add(user)
    return user.id


async def make_article(
    factory: SessionFactory,
    blocks: list[tuple[UUID, str]],
    status: ArticleStatus = ArticleStatus.CANONICAL,
    title: str = "Leczenie warrozy",
    consents: list[ConsentStatus] | None = None,
) -> tuple[UUID, list[UUID]]:
    """An article with one block per ``(author_id, content)``. Returns (article, block ids)."""
    article_id = uuid7()
    block_ids: list[UUID] = []
    async with factory() as session, session.begin():
        session.add(WikiArticle(id=article_id, title=title, status=status))
        await session.flush()
        for position, (author_id, content) in enumerate(blocks):
            block_id = uuid7()
            block_ids.append(block_id)
            session.add(
                ArticleBlock(
                    id=block_id,
                    article_id=article_id,
                    position=position,
                    author_id=author_id,
                    consent_status=consents[position] if consents else ConsentStatus.ACTIVE,
                    content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
    return article_id, block_ids
