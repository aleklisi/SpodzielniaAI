"""Chunk 7.2: routing by a configuration source, the gateway's own checks against a tampered
configuration, hot swapping of transports, and resolving a stored snapshot. Chunk 10.1: private
purposes need deployments cleared for private data, wherever they run."""

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
    PRIVATE_PURPOSES,
    Deployment,
    LiteLLMGateway,
    LiteLLMTransport,
    LLMError,
    LLMPurpose,
    ModelUnavailable,
    ResolvedLlmConfig,
    StaticConfig,
    env_config,
)
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot, SnapshotDeployment

MESSAGES: list[Any] = [{"role": "user", "content": "x"}]
LOCAL = Deployment("vllm/qwen", "qwen", "http://vllm:8000/v1", None, local=True)
CLOUD = Deployment("cloud/gpt", "gpt", "https://api.example.com/v1", "sk", local=False)
CLEARED_CLOUD = Deployment(
    "mistral/small", "small", "https://api.mistral.ai/v1", "sk", local=False, private_data=True
)
UNCLEARED_LOCAL = Deployment(
    "lmstudio/qwen", "qwen", "http://lan:1234/v1", None, local=True, private_data=False
)


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


@pytest.mark.parametrize("purpose", sorted(PRIVATE_PURPOSES))
@pytest.mark.parametrize("deployment", [CLOUD, UNCLEARED_LOCAL], ids=["cloud", "local"])
async def test_a_route_to_an_uncleared_deployment_is_refused(
    purpose: LLMPurpose, deployment: Deployment
) -> None:
    gateway, transport = _gateway(_config({purpose: deployment}), allow_cloud=True)
    before = _refused(purpose)
    call = (
        gateway.embed(["x"])
        if purpose == LLMPurpose.EMBED
        else gateway.complete(MESSAGES, purpose=purpose, private_data=True)
    )
    with pytest.raises(ModelUnavailable):
        await call
    assert transport.calls == []
    after = _refused(purpose)
    assert after == before + 1


@pytest.mark.parametrize("purpose", sorted(PRIVATE_PURPOSES - {LLMPurpose.EMBED}))
async def test_a_cleared_cloud_deployment_serves_private_purposes(purpose: LLMPurpose) -> None:
    gateway, transport = _gateway(_config({purpose: CLEARED_CLOUD}), allow_cloud=True)
    result = await gateway.complete(MESSAGES, purpose=purpose, private_data=True)
    assert result.deployment == "mistral/small"
    assert transport.calls[0][0] == "mistral/small"


async def test_a_cleared_cloud_deployment_still_needs_allow_cloud() -> None:
    gateway, transport = _gateway(_config({LLMPurpose.PROMOTION: CLEARED_CLOUD}))
    with pytest.raises(ModelUnavailable):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.PROMOTION, private_data=True)
    assert transport.calls == []


async def test_private_data_refuses_an_uncleared_rag_route() -> None:
    gateway, transport = _gateway(_config({LLMPurpose.RAG: CLOUD}), allow_cloud=True)
    with pytest.raises(ModelUnavailable):
        await gateway.complete(MESSAGES, purpose=LLMPurpose.RAG, private_data=True)
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
    await gateway.complete(MESSAGES, purpose=LLMPurpose.PROMOTION, private_data=True)
    (rag_model, rag), (promo_model, promo) = transport.calls
    assert (rag_model, rag["temperature"], rag["max_tokens"]) == ("vllm/qwen", 0.7, 4096)
    assert (promo_model, promo["temperature"], promo["max_tokens"]) == ("vllm/bielik", 0.0, 512)


async def test_unrouted_purposes_are_unavailable() -> None:
    gateway, _ = _gateway(_config({LLMPurpose.RAG: LOCAL}))
    with pytest.raises(ModelUnavailable):
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
    with pytest.raises(ModelUnavailable):
        gateway.route(LLMPurpose.PROMOTION, private_data=True)
    with pytest.raises(ModelUnavailable):
        gateway.route(LLMPurpose.PII_SCRUB, private_data=True)
    assert gateway.route(LLMPurpose.RAG, private_data=False).name == "p/bielik"


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


def test_resolve_takes_clearance_from_the_provider_row_within_the_env_list() -> None:
    settings = SETTINGS.model_copy(
        update={
            "cloud_hosts": ["api.example.com", "api.mistral.ai"],
            "private_data_endpoints": ["http://vllm:8000", "https://api.mistral.ai"],
        }
    )
    lan = _provider("http://vllm:8000/v1", LlmProviderKind.LOCAL)
    lan.private_data_cleared = True
    mistral = _provider("https://api.mistral.ai/v1", LlmProviderKind.CLOUD)
    mistral.private_data_cleared = True
    other = _provider("https://api.example.com/v1", LlmProviderKind.CLOUD)
    other.private_data_cleared = True  # cleared in the database, but not on the env list
    plain = _provider("http://vllm:8000/v1", LlmProviderKind.LOCAL)  # on the list, not cleared
    providers = [lan, mistral, other, plain]
    for index, provider in enumerate(providers):
        provider.name = f"p{index}"
    snapshot = ConfigSnapshot(
        routes={
            LlmRoutePurpose.PROMOTION: "a",
            LlmRoutePurpose.PRIVATE_NOTE: "b",
            LlmRoutePurpose.PII_SCRUB: "c",
            LlmRoutePurpose.VLM: "d",
        },
        deployments={
            "a": _item(lan, "bielik", LlmProviderKind.LOCAL, private_data=True),
            "b": _item(mistral, "small", LlmProviderKind.CLOUD, private_data=True),
            "c": _item(other, "gpt", LlmProviderKind.CLOUD, private_data=True),
            "d": _item(plain, "vl", LlmProviderKind.LOCAL, private_data=True),
        },
    )
    config = resolve(9, snapshot, {p.id: p for p in providers}, _cipher(), settings)
    cleared = {d.name for d in config.deployments.values() if d.cleared}
    assert cleared == {"p0/bielik", "p1/small"}
    gateway = LiteLLMGateway(settings, Recorder(), source=StaticConfig(config))
    gateway._config = config  # pyright: ignore[reportPrivateUsage]
    assert gateway.route(LLMPurpose.PROMOTION, private_data=True).name == "p0/bielik"
    assert gateway.route(LLMPurpose.PRIVATE_NOTE, private_data=True).name == "p1/small"
    for purpose in (LLMPurpose.PII_SCRUB, LLMPurpose.VLM):
        with pytest.raises(ModelUnavailable):
            gateway.route(purpose, private_data=True)


def test_env_config_clears_the_local_endpoint_only_within_the_list() -> None:
    assert env_config(LlmSettings(chat_model="c")).deployments["local-chat"].cleared
    off = env_config(LlmSettings(chat_model="c", private_data_endpoints=[]))
    assert not off.deployments["local-chat"].cleared
    cloud = env_config(
        LlmSettings(
            allow_cloud=True,
            cloud_model="gpt",
            cloud_base_url="https://api.example.com/v1",
            private_data_endpoints=["https://api.example.com"],
        )
    )
    assert not cloud.deployments["cloud-chat"].cleared  # .env has no clearance step
