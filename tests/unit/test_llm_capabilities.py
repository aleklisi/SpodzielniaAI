"""Chunk 10.2: "not configured" is its own state: features off, work waits, nothing falls back."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.llm.capabilities import (
    FEATURES,
    builtin_purposes,
    capabilities,
    missing_purposes,
)
from spoldzielnia_ai.llm.gateway import (
    Deployment,
    LiteLLMGateway,
    LLMPurpose,
    ModelNotConfigured,
    ModelUnavailable,
    ResolvedLlmConfig,
    StaticConfig,
)
from spoldzielnia_ai.workers.engines.base import WaitingForModel
from spoldzielnia_ai.workers.engines.vision import GatewayVisionEngine, UnavailableVisionEngine
from tests.fixtures import files
from tests.fixtures.llm import FakeLLMGateway

if TYPE_CHECKING:
    from pathlib import Path

MESSAGES: list[Any] = [{"role": "user", "content": "x"}]


def _gateway(routes: dict[LLMPurpose, Deployment], **settings: Any) -> LiteLLMGateway:
    config = ResolvedLlmConfig(
        3, {d.name: d for d in routes.values()}, {p: d.name for p, d in routes.items()}
    )
    gateway = LiteLLMGateway(LlmSettings(**settings), source=StaticConfig(config))
    gateway._config = config  # pyright: ignore[reportPrivateUsage]
    return gateway


LOCAL = Deployment("lm/bielik", "bielik", "http://llm:11434/v1", None, local=True)
CLOUD = Deployment("cloud/gpt", "gpt", "https://api.example.com/v1", "sk", local=False)


@pytest.mark.parametrize("purpose", [LLMPurpose.RAG, LLMPurpose.PROMOTION, LLMPurpose.EMBED])
def test_an_unrouted_purpose_is_not_configured(purpose: LLMPurpose) -> None:
    gateway = _gateway({})
    with pytest.raises(ModelNotConfigured) as exc:
        gateway.route(purpose, private_data=False)
    assert exc.value.purpose == purpose
    assert isinstance(exc.value, ModelUnavailable)  # existing handlers keep working
    assert not gateway.configured(purpose)


def test_configured_follows_the_gateway_rules() -> None:
    gateway = _gateway({LLMPurpose.RAG: CLOUD, LLMPurpose.PROMOTION: CLOUD}, allow_cloud=True)
    assert gateway.configured(LLMPurpose.RAG)
    assert not gateway.configured(LLMPurpose.PROMOTION)  # private: the cloud is not cleared
    assert not gateway.configured(LLMPurpose.RAG, private_data=True)
    ceiling = _gateway({LLMPurpose.RAG: CLOUD}, allow_cloud=False)
    assert not ceiling.configured(LLMPurpose.RAG)
    with pytest.raises(ModelNotConfigured):
        ceiling.route(LLMPurpose.RAG, private_data=False)


async def test_capabilities_name_features_whose_purposes_all_have_models() -> None:
    gateway = LiteLLMGateway(LlmSettings(chat_model="bielik"))
    features = await capabilities(gateway)
    assert features == {
        "wiki_answers": False,  # needs EMBED too
        "notebook_answers": False,
        "notebook_search": False,
        "promotion": True,
        "image_descriptions": False,
        "recognition": False,
        "transcription": False,
        "ocr": False,
    }
    assert set(features) == set(FEATURES)
    full = LiteLLMGateway(
        LlmSettings(chat_model="b", vlm_model="v", embed_model="e", transcribe_model="w")
    )
    assert all((await capabilities(full)).values())
    assert await missing_purposes(full) == []
    nothing_cleared = LiteLLMGateway(
        LlmSettings(
            chat_model="b",
            vlm_model="v",
            embed_model="e",
            transcribe_model="w",
            private_data_endpoints=[],
        )
    )
    assert await missing_purposes(nothing_cleared) == [
        LLMPurpose.PII_SCRUB,
        LLMPurpose.PRIVATE_NOTE,
        LLMPurpose.PROMOTION,
        LLMPurpose.VLM,
        LLMPurpose.EMBED,
        LLMPurpose.TRANSCRIBE,
        LLMPurpose.OCR,
    ]


async def test_the_builtin_whisper_counts_as_transcription() -> None:
    settings = LlmSettings(chat_model="bielik")
    gateway = LiteLLMGateway(settings)
    assert (await capabilities(gateway, builtin_purposes(settings)))["transcription"] is True
    assert LLMPurpose.TRANSCRIBE not in await missing_purposes(gateway, builtin_purposes(settings))
    off = settings.model_copy(update={"builtin_transcription": False})
    assert (await capabilities(gateway, builtin_purposes(off)))["transcription"] is False


def test_images_wait_for_a_vision_model(tmp_path: Path) -> None:
    path = tmp_path / "ul.png"
    path.write_bytes(files.png())
    with pytest.raises(WaitingForModel) as exc:
        GatewayVisionEngine(lambda: FakeLLMGateway(configured=False)).extract(path, "image/png")
    assert exc.value.purpose == "VLM"
    with pytest.raises(WaitingForModel):
        UnavailableVisionEngine().extract(path, "image/png")
