"""Chunk 7.2: routing by a configuration source, the gateway's own checks against a tampered
configuration, hot swapping of transports, and resolving a stored snapshot."""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from pydantic import SecretStr

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import LlmCapability, LlmProviderKind, LlmRoutePurpose
from spoldzielnia_ai.db.models import LlmProvider
from spoldzielnia_ai.llm import gateway as gateway_module
from spoldzielnia_ai.llm.config_store import resolve
from spoldzielnia_ai.llm.gateway import (
    ALWAYS_LOCAL,
    Deployment,
    LiteLLMGateway,
    LiteLLMTransport,
    LLMError,
    LLMPurpose,
    LocalModelUnavailable,
    ResolvedLlmConfig,
    StaticConfig,
    env_config,
)
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot, SnapshotDeployment

MESSAGES: list[Any] = [{"role": "user", "content": "x"}]
LOCAL = Deployment("vllm/qwen", "qwen", "http://vllm:8000/v1", None, local=True)
CLOUD = Deployment("cloud/gpt", "gpt", "https://api.example.com/v1", "sk", local=False)


@dataclass
class Recorder:
    calls: list[tuple[str, dict[str, Any]]] = field(
        default_factory=list[tuple[str, dict[str, Any]]]
    )
    gate: asyncio.Event | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append((model, kwargs))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        message = SimpleNamespace(content=f"from {model}")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        return SimpleNamespace(data=[{"embedding": [1.0]} for _ in input])


def _config(routes: dict[LLMPurpose, Deployment], revision: int = 7) -> ResolvedLlmConfig:
    return ResolvedLlmConfig(
        revision, {d.name: d for d in routes.values()}, {p: d.name for p, d in routes.items()}
    )


def _gateway(config: ResolvedLlmConfig, **settings: Any) -> tuple[LiteLLMGateway, Recorder]:
    transport = Recorder()
    gateway = LiteLLMGateway(LlmSettings(**settings), transport, source=StaticConfig(config))
    return gateway, transport


def _refused(purpose: LLMPurpose) -> float:
    value = REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": purpose.value})
    return value or 0.0


@pytest.mark.parametrize("purpose", sorted(ALWAYS_LOCAL))
async def test_a_tampered_route_to_the_cloud_is_refused(purpose: LLMPurpose) -> None:
    gateway, transport = _gateway(_config({purpose: CLOUD}), allow_cloud=True)
    before = _refused(purpose)
    with pytest.raises(LocalModelUnavailable):
        await gateway.complete(MESSAGES, purpose=purpose, force_local_llm=True)
    assert transport.calls == []
    after = _refused(purpose)
    assert after == before + 1


async def test_force_local_refuses_a_cloud_rag_route() -> None:
    gateway, transport = _gateway(_config({LLMPurpose.RAG: CLOUD}), allow_cloud=True)
    with pytest.raises(LocalModelUnavailable):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG, force_local_llm=True)
    assert transport.calls == []


async def test_the_env_ceiling_beats_the_configuration() -> None:
    gateway, transport = _gateway(_config({LLMPurpose.RAG: CLOUD}), allow_cloud=False)
    with pytest.raises(LLMError):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    allowed, transport = _gateway(_config({LLMPurpose.RAG: CLOUD}), allow_cloud=True)
    result = await allowed.complete(MESSAGES, purpose=LLMPurpose.RAG)
    assert result.deployment == "cloud/gpt"
    assert transport.calls[0][0] == "cloud/gpt"


async def test_each_purpose_has_its_own_model_and_parameters() -> None:
    tuned = Deployment(
        "vllm/bielik",
        "bielik",
        "http://vllm:8000/v1",
        None,
        local=True,
        temperature=0.0,
        max_tokens=512,
        structured_output="prompt",
    )
    config = _config({LLMPurpose.RAG: LOCAL, LLMPurpose.PROMOTION: tuned})
    gateway, transport = _gateway(config, temperature=0.7, max_tokens=4096)
    await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    await gateway.complete(MESSAGES, purpose=LLMPurpose.PROMOTION, force_local_llm=True)
    (rag_model, rag), (promo_model, promo) = transport.calls
    assert (rag_model, rag["temperature"], rag["max_tokens"]) == ("vllm/qwen", 0.7, 4096)
    assert (promo_model, promo["temperature"], promo["max_tokens"]) == ("vllm/bielik", 0.0, 512)


async def test_unrouted_purposes_are_unavailable() -> None:
    gateway, _ = _gateway(_config({LLMPurpose.RAG: LOCAL}))
    with pytest.raises(LocalModelUnavailable):
        await gateway.embed(["x"])
    with pytest.raises(LLMError):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.PLAYGROUND)


def test_env_config_matches_the_pre_stage_7_routing() -> None:
    config = env_config(
        LlmSettings(
            vlm_model="vl",
            embed_model="emb",
            allow_cloud=True,
            cloud_model="gpt",
            cloud_base_url="https://api.example.com/v1",
        )
    )
    assert config.revision == 0
    assert dict(config.routes) == {
        LLMPurpose.VLM: "local-vlm",
        LLMPurpose.EMBED: "local-embed",
        LLMPurpose.RAG: "cloud-chat",  # no local chat model: only RAG may use the cloud
    }


@dataclass
class Switchable:
    config: ResolvedLlmConfig

    async def current(self) -> ResolvedLlmConfig:
        return self.config


async def test_a_new_revision_swaps_the_transport_and_calls_in_flight_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[tuple[int, Recorder]] = []
    first = Recorder(gate=asyncio.Event())

    def fake_transport(config: ResolvedLlmConfig, _settings: LlmSettings) -> Recorder:
        recorder = first if not built else Recorder()
        built.append((config.revision, recorder))
        return recorder

    monkeypatch.setattr(gateway_module, "LiteLLMTransport", fake_transport)
    other = Deployment("vllm/llama", "llama", "http://vllm:8000/v1", None, local=True)
    source = Switchable(_config({LLMPurpose.RAG: LOCAL}, revision=1))
    gateway = LiteLLMGateway(LlmSettings(), source=source)

    slow = asyncio.create_task(gateway.complete(MESSAGES, purpose=LLMPurpose.RAG))
    await first.entered.wait()
    source.config = _config({LLMPurpose.RAG: other}, revision=2)
    fast = await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG)
    assert first.gate is not None
    first.gate.set()
    assert (await slow).content == "from vllm/qwen"
    assert fast.content == "from vllm/llama"
    assert [revision for revision, _ in built] == [1, 2]
    assert gateway.config.revision == 2


def test_cloud_deployments_go_through_the_egress_proxy() -> None:
    settings = LlmSettings(egress_proxy_url="http://llm-egress:3128")
    transport = LiteLLMTransport(_config({LLMPurpose.RAG: CLOUD, LLMPurpose.VLM: LOCAL}), settings)
    cloud = transport._params("cloud/gpt")  # pyright: ignore[reportPrivateUsage]
    local = transport._params("vllm/qwen")  # pyright: ignore[reportPrivateUsage]
    assert "client" not in local
    assert local["api_base"] == "http://vllm:8000/v1"
    client = cloud["client"]
    assert str(client.base_url).rstrip("/") == "https://api.example.com/v1"
    proxy_transport = client._client._transport  # pyright: ignore[reportUnknownMemberType]
    assert proxy_transport is not None
    assert cloud["model"] == "openai/gpt"
    without = LiteLLMTransport(_config({LLMPurpose.RAG: CLOUD}), LlmSettings())
    assert "client" not in without._params("cloud/gpt")  # pyright: ignore[reportPrivateUsage]


# --- resolve ------------------------------------------------------------------------------------

SETTINGS = LlmSettings(
    local_endpoints=["http://vllm:8000"],
    cloud_hosts=["api.example.com"],
    api_key=SecretStr("env-local-key"),
    allow_cloud=True,
)


def _provider(base_url: str, kind: LlmProviderKind, *, inherit: bool = False) -> LlmProvider:
    return LlmProvider(id=uuid4(), name="p", kind=kind, base_url=base_url, inherit_env_key=inherit)


def _item(
    provider: LlmProvider, model: str, kind: LlmProviderKind, **extra: Any
) -> SnapshotDeployment:
    return SnapshotDeployment(
        provider_id=provider.id,
        provider_name=provider.name,
        model_id=uuid4(),
        model=model,
        capability=extra.pop("capability", LlmCapability.CHAT),
        base_url=provider.base_url,
        kind=kind,
        inherit_env_key=provider.inherit_env_key,
        **extra,
    )


def _cipher() -> EnvelopeCipher:
    return EnvelopeCipher(StaticKeyProvider(base64.b64encode(os.urandom(32)).decode()))


def test_resolve_derives_locality_again_and_drops_disallowed_endpoints() -> None:
    local = _provider("http://vllm:8000/v1", LlmProviderKind.LOCAL, inherit=True)
    liar = _provider("https://api.example.com/v1", LlmProviderKind.LOCAL)
    gone = _provider("http://10.1.2.3:8000/v1", LlmProviderKind.LOCAL)
    items = {
        "a": _item(local, "bielik", LlmProviderKind.LOCAL, temperature=0.1),
        "b": _item(liar, "gpt", LlmProviderKind.LOCAL),  # the snapshot claims LOCAL
        "c": _item(gone, "old", LlmProviderKind.LOCAL),
    }
    snapshot = ConfigSnapshot(
        routes={
            LlmRoutePurpose.PROMOTION: "b",
            LlmRoutePurpose.RAG: "a",
            LlmRoutePurpose.PII_SCRUB: "c",
        },
        deployments=items,
    )
    providers = {p.id: p for p in (local, liar, gone)}
    config = resolve(5, snapshot, providers, _cipher(), SETTINGS)

    assert config.revision == 5
    assert set(config.deployments) == {"p/bielik", "p/gpt"}
    assert config.deployments["p/gpt"].local is False
    assert config.deployments["p/bielik"].api_key == "env-local-key"
    assert config.deployments["p/bielik"].temperature == 0.1
    assert LLMPurpose.PII_SCRUB not in config.routes
    gateway = LiteLLMGateway(SETTINGS, Recorder(), source=StaticConfig(config))
    gateway._config = config  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(LocalModelUnavailable):
        gateway.route(LLMPurpose.PROMOTION, force_local_llm=True)
    with pytest.raises(LocalModelUnavailable):
        gateway.route(LLMPurpose.PII_SCRUB, force_local_llm=True)
    assert gateway.route(LLMPurpose.RAG, force_local_llm=False).name == "p/bielik"


def test_resolve_skips_unreadable_keys_and_missing_providers() -> None:
    provider = _provider("https://api.example.com/v1", LlmProviderKind.CLOUD)
    provider.api_key_wrapped = b"\x01\x00garbage"
    provider.api_key_ciphertext = b"x" * 40
    missing = _provider("http://vllm:8000/v1", LlmProviderKind.LOCAL)
    snapshot = ConfigSnapshot(
        routes={LlmRoutePurpose.RAG: "a", LlmRoutePurpose.PROMOTION: "b"},
        deployments={
            "a": _item(provider, "gpt", LlmProviderKind.CLOUD),
            "b": _item(missing, "bielik", LlmProviderKind.LOCAL),
        },
    )
    config = resolve(3, snapshot, {provider.id: provider}, _cipher(), SETTINGS)
    assert dict(config.routes) == {}
    assert dict(config.deployments) == {}
