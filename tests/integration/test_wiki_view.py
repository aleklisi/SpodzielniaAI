"""Chunk 6.3 backend: the article view names its approving experts, and canonical text can be
watermarked per reader (``shield.watermark_wiki``)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.db.enums import ArticleStatus
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.shield.watermark import Watermarker
from tests.fixtures.governance import approve, governance_harness, login_expert
from tests.fixtures.wiki import make_article

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance, QdrantInstance

pytestmark = pytest.mark.integration

TEXT = (
    "Ponadto należy regularnie sprawdzać osyp. Jednak w przypadku silnej inwazji warto "
    "zastosować odpowiednie preparaty, gdyż ryzyko jest duże."
)
KEY = SecretStr("wiki-watermark-test-key")


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.mark.parametrize("watermark", [False, True])
async def test_article_view_lists_approving_experts_and_watermarks_on_request(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
    watermark: bool,
) -> None:
    shield = ShieldSettings(watermark_key=KEY, watermark_wiki=watermark)
    settings = settings.model_copy(update={"shield": shield})
    async with governance_harness(settings, factory, qdrant, qdrant_server.url) as h:
        author = await h.login()
        expert = await login_expert(h)
        article_id, _ = await make_article(
            factory, [(UUID(author.user_id), TEXT)], status=ArticleStatus.IN_REVIEW
        )
        await approve(expert, article_id)
        reader = await h.login()
        as_reader = (await reader.client.get(f"/wiki/articles/{article_id}")).json()
        as_author = (await author.client.get(f"/wiki/articles/{article_id}")).json()

    assert [e["id"] for e in as_reader["approving_experts"]] == [expert.user_id]
    assert as_author["blocks"][0]["content"] == TEXT  # authors always see their own words
    shown = as_reader["blocks"][0]["content"]
    marker = Watermarker(KEY.get_secret_value().encode())
    if watermark:
        assert shown == marker.apply(TEXT, UUID(reader.user_id))
    else:
        assert shown == TEXT
