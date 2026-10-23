"""Chunk 10.8 (P4): members see where their private data is processed, and a newly cleared
cloud provider shows a one-time banner."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import LlmCapability
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.config_service import ClearanceIn, LlmConfigService, ModelIn, ProviderIn
from tests.fixtures.app import SITE, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness

pytestmark = pytest.mark.integration

LLM = LlmSettings(
    chat_model="bielik-11b",
    transcribe_model="whisper",
    allow_cloud=True,
    cloud_hosts=["api.mistral.ai", "api.example.com"],
    private_data_endpoints=[
        "http://llm:11434",
        "https://api.mistral.ai",
        "https://api.example.com",
    ],
    builtin_ocr=True,
    builtin_transcription=True,
    builtin_image_embed=False,
)
KEY = "sk-live-never-shown-0001"
CLEARANCE = ClearanceIn(region="UE (Francja)", retention="30 dni", contract_ref="DPA 2026/07")


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory) -> AsyncIterator[Harness]:
    async with harness(settings.model_copy(update={"llm": LLM}), factory) as h:
        yield h


def _service(h: Harness, factory: SessionFactory) -> LlmConfigService:
    return LlmConfigService(factory, h.app.state.cipher, LLM)


async def _cloud(service: LlmConfigService, name: str, url: str) -> None:
    provider = await service.create_provider(
        None, ProviderIn(name=name, base_url=url, api_key=SecretStr(KEY))
    )
    await service.create_model(
        None, ModelIn(provider_id=provider.id, model_name="m", capability=LlmCapability.CHAT)
    )
    await service.clear_provider(None, provider.id, CLEARANCE)


async def test_members_see_cleared_providers_and_a_one_time_banner(
    h: Harness, factory: SessionFactory
) -> None:
    service = _service(h, factory)
    await service.ensure_seeded(None)
    member = await h.login()

    first = (await member.client.get("/privacy/processors")).json()
    (local,) = first["processors"]
    assert (local["name"], local["host"], local["on_server"]) == ("local", "llm", True)
    assert {"PII_SCRUB", "PRIVATE_NOTE", "PROMOTION", "TRANSCRIBE"} <= set(local["purposes"])
    assert "RAG" not in local["purposes"]  # public wiki answers are not private data
    assert first["built_in"] == ["OCR"]  # Docling's OCR on the server; transcription is routed
    assert first["banner"] is False  # nothing in the cloud

    await _cloud(service, "mistral", "https://api.mistral.ai/v1")
    after = await member.client.get("/privacy/processors")
    body = after.json()
    mistral = next(p for p in body["processors"] if p["name"] == "mistral")
    assert mistral == {
        "name": "mistral",
        "host": "api.mistral.ai",
        "on_server": False,
        "region": "UE (Francja)",
        "retention": "30 dni",
        "contract_ref": "DPA 2026/07",
        "cleared_since": mistral["cleared_since"],
        "purposes": [],  # cleared, not routed yet
    }
    assert body["banner"] is True
    assert KEY not in after.text
    assert "/v1" not in after.text

    seen = await member.client.post("/privacy/processors/seen", headers=member.headers)
    assert seen.status_code == 204
    assert (await member.client.get("/privacy/processors")).json()["banner"] is False
    other = await h.login()  # every member gets the banner once
    assert (await other.client.get("/privacy/processors")).json()["banner"] is True

    await _cloud(service, "example", "https://api.example.com/v1")  # a new one: again
    assert (await member.client.get("/privacy/processors")).json()["banner"] is True


async def test_uncleared_or_disabled_providers_are_not_listed(
    h: Harness, factory: SessionFactory
) -> None:
    service = _service(h, factory)
    await service.ensure_seeded(None)
    await _cloud(service, "mistral", "https://api.mistral.ai/v1")
    mistral = next(p for p in await service.providers() if p.name == "mistral")
    await service.unclear_provider(None, mistral.id)
    member = await h.login()
    names = [
        p["name"] for p in (await member.client.get("/privacy/processors")).json()["processors"]
    ]
    assert names == ["local"]
    async with AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE) as anonymous:
        assert (await anonymous.get("/privacy/processors")).status_code == 401
