"""Chunk 10.6 (unit): image embeddings over an API, the probe, the policy and the features."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from prometheus_client import REGISTRY

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import LlmCapability, LlmProviderKind
from spoldzielnia_ai.llm.capabilities import builtin_purposes, capabilities
from spoldzielnia_ai.llm.gateway import (
    LOCAL_IMAGE_EMBED,
    Deployment,
    LiteLLMGateway,
    LLMOutputInvalid,
    LLMPurpose,
    ModelNotConfigured,
    ModelRejected,
    ModelUnavailable,
    env_config,
)
from spoldzielnia_ai.llm.probes import deployment_for, probe_model

URL = "http://llm:11434/v1/embeddings"
PIC = "data:image/jpeg;base64,AAAA"
SIGLIP = Deployment("lm/siglip", "siglip", "http://llm:11434/v1", None, local=True)


def _reply(*vectors: list[float]) -> httpx.Response:
    data = [{"index": i, "embedding": v} for i, v in reversed(list(enumerate(vectors)))]
    return httpx.Response(200, json={"data": data})


@respx.mock
async def test_images_go_inline_and_come_back_normalised_in_order() -> None:
    route = respx.post(URL).mock(return_value=_reply([3.0, 4.0], [0.0, 2.0]))
    gateway = LiteLLMGateway(LlmSettings())
    vectors = await gateway.embed_images([PIC, PIC], deployment=SIGLIP)
    assert vectors == [[0.6, 0.8], [0.0, 1.0]]  # by index, unit length
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "model": "siglip",
        "input": [PIC, PIC],
        "modality": "image",
        "encoding_format": "float",
    }


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(400), ModelRejected),
        (httpx.Response(429), ModelUnavailable),
        (httpx.Response(502), ModelUnavailable),
        (httpx.Response(200, json={"data": []}), LLMOutputInvalid),
        (httpx.Response(200, json={"data": [{"index": 0, "embedding": [0, 0]}]}), LLMOutputInvalid),
    ],
)
@respx.mock
async def test_errors(response: httpx.Response, error: type[Exception]) -> None:
    respx.post(URL).mock(return_value=response)
    with pytest.raises(error):
        await LiteLLMGateway(LlmSettings()).embed_images([PIC], deployment=SIGLIP)


@respx.mock
async def test_an_uncleared_provider_never_receives_a_photo() -> None:
    route = respx.post(url__regex=r".*").mock(return_value=_reply([1.0]))
    uncleared = Deployment(
        "x/clip", "clip", "http://other:1/v1", None, local=True, private_data=False
    )
    cloud = Deployment("c/clip", "clip", "https://api.x.ai/v1", "k", local=False, private_data=True)
    before = REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": "IMAGE_EMBED"}) or 0
    gateway = LiteLLMGateway(LlmSettings())
    with pytest.raises(ModelNotConfigured):
        await gateway.embed_images([PIC], deployment=uncleared)
    with pytest.raises(ModelNotConfigured):  # private even if the caller says otherwise (P1)
        await gateway.embed_images([PIC], deployment=uncleared, private_data=False)
    with pytest.raises(ModelNotConfigured):  # no cloud without allow_cloud (S2)
        await gateway.embed_images([PIC], deployment=cloud)
    after = REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": "IMAGE_EMBED"}) or 0
    assert after == before + 2
    assert route.call_count == 0


def test_env_routes_image_embeddings_only_to_their_own_model() -> None:
    assert LLMPurpose.IMAGE_EMBED not in env_config(LlmSettings(embed_model="text")).routes
    routes = env_config(LlmSettings(embed_model="text", image_embed_model="clip")).routes
    assert routes[LLMPurpose.IMAGE_EMBED] == LOCAL_IMAGE_EMBED


@pytest.mark.parametrize("separates", [True, False])
@respx.mock
async def test_the_probe_records_the_dimension_and_the_order(separates: bool) -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        same, other = ([1.0, 0.1, 0.0], [0.0, 1.0, 0.0])
        return _reply([1.0, 0.0, 0.0], same if separates else other, other if separates else same)

    respx.post("http://vllm:8000/v1/embeddings").mock(side_effect=reply)
    deployment = deployment_for(
        "v/clip", "clip", "http://vllm:8000/v1", None, LlmProviderKind.LOCAL
    )
    settings = LlmSettings(local_endpoints=["http://vllm:8000"])
    report: dict[str, Any] = await probe_model(LlmCapability.IMAGE_EMBED, deployment, settings)
    assert report["ok"] is separates
    assert report["dimension"] == 3


async def test_recognition_needs_an_image_model_the_builtin_counts() -> None:
    settings = LlmSettings(vlm_model="qwen-vl")
    gateway = LiteLLMGateway(settings)
    assert (await capabilities(gateway, builtin_purposes(settings)))["recognition"] is True
    off = settings.model_copy(update={"builtin_image_embed": False})
    assert (await capabilities(gateway, builtin_purposes(off)))["recognition"] is False
    routed = LiteLLMGateway(off.model_copy(update={"image_embed_model": "clip"}))
    assert (await capabilities(routed, builtin_purposes(off)))["recognition"] is True
