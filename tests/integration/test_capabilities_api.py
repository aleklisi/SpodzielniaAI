"""Chunk 10.2: ``/capabilities`` and features that are off without a model (P5)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.vector.generations import StaticGenerations
from tests.fixtures.app import SITE, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_every_feature_is_on_when_its_purposes_have_models(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        member = await h.login()
        response = await member.client.get("/capabilities")
        assert response.status_code == 200
        assert all(response.json()["features"].values())
        transport = ASGITransport(app=h.app)
        async with AsyncClient(transport=transport, base_url=SITE) as anonymous:
            assert (await anonymous.get("/capabilities")).status_code == 401


async def test_without_models_features_are_off_and_refused_up_front(
    settings: Settings, factory: SessionFactory
) -> None:
    bare = settings.model_copy(
        update={"llm": LlmSettings(builtin_transcription=False, builtin_ocr=False)}
    )
    async with harness(bare, factory) as h:
        h.app.state.generations = StaticGenerations(())  # no embedding model, no index (10.3)
        member = await h.login(amr=["pwd", "otp"])
        features = (await member.client.get("/capabilities")).json()["features"]
        assert not any(features.values())

        promote = await member.client.post(
            "/notes/promote-to-wiki", json={"note_ids": [str(uuid4())]}, headers=member.headers
        )
        recognize = await member.client.post(
            f"/notes/{uuid4()}/attachments/{uuid4()}/recognize", json={}, headers=member.headers
        )
        ask = await member.client.post(
            "/notes/ask", json={"question": "Kiedy dokarmiałem ul nr 3?"}, headers=member.headers
        )
        for response in (promote, recognize, ask):
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == {"error": "model_not_configured"}
            assert "retry-after" not in response.headers  # retrying won't help


async def test_only_the_features_missing_a_purpose_are_off(
    settings: Settings, factory: SessionFactory
) -> None:
    chat_only = settings.model_copy(update={"llm": LlmSettings(chat_model="bielik")})
    async with harness(chat_only, factory) as h:
        member = await h.login()
        features = (await member.client.get("/capabilities")).json()["features"]
        assert features["promotion"] is True
        assert features["wiki_answers"] is False  # needs embeddings
        assert features["image_descriptions"] is False
