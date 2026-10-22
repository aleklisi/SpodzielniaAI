"""Chunk 10.3 (unit): embeddings pinned to an index generation's model, P8."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from prometheus_client import REGISTRY

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import IndexGenerationStatus
from spoldzielnia_ai.llm.gateway import Deployment, LiteLLMGateway, ModelNotConfigured
from spoldzielnia_ai.vector.collections import family_of, generation_collection
from spoldzielnia_ai.vector.embedding import Embedder, EmbeddingMismatch
from spoldzielnia_ai.vector.generations import StaticGenerations, embed_key
from tests.fixtures.llm import FakeLLMGateway, make_generation

Status = IndexGenerationStatus
ROUTED = LlmSettings(embed_model="model-b")  # EMBED is routed to model-b (local, cleared)
OLD = Deployment("lm/model-a", "model-a", "http://llm:11434/v1", None, local=True)


def _refused() -> float:
    return REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": "EMBED"}) or 0.0


@dataclass
class Recording:
    embedded_with: list[str] = field(default_factory=list[str])

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        raise AssertionError("no chat in these tests")

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        self.embedded_with.append(model)
        return SimpleNamespace(data=[{"embedding": [1.0, 0.0, 0.0]} for _ in input])


async def test_a_pinned_deployment_is_used_instead_of_the_route() -> None:
    transport = Recording()
    gateway = LiteLLMGateway(ROUTED, transport)

    await gateway.embed(["x"], deployment=OLD)
    await gateway.embed(["y"])

    assert transport.embedded_with == ["lm/model-a", "local-embed"]


async def test_a_pinned_deployment_obeys_the_private_data_policy() -> None:
    transport = Recording()
    gateway = LiteLLMGateway(ROUTED, transport)
    uncleared = Deployment(
        "x/emb", "emb", "http://other:1234/v1", None, local=True, private_data=False
    )
    before = _refused()

    with pytest.raises(ModelNotConfigured):
        await gateway.embed(["tajne"], deployment=uncleared)

    assert _refused() == before + 1
    assert transport.embedded_with == []  # refused, never sent; no fallback to the route


async def test_a_pinned_cloud_deployment_needs_allow_cloud() -> None:
    transport = Recording()
    cloud = Deployment(
        "mistral/emb",
        "mistral-embed",
        "https://api.mistral.ai/v1",
        "k",
        local=False,
        private_data=True,
    )
    with pytest.raises(ModelNotConfigured):
        await LiteLLMGateway(ROUTED, transport).embed(["x"], deployment=cloud)
    allowed = LiteLLMGateway(ROUTED.model_copy(update={"allow_cloud": True}), transport)
    await allowed.embed(["x"], deployment=cloud)
    assert transport.embedded_with == ["mistral/emb"]


async def test_the_embedder_uses_each_generations_own_model() -> None:
    llm = FakeLLMGateway()
    serving = make_generation(1, deployment=OLD)
    building = make_generation(
        2,
        status=Status.BUILDING,
        collections=("private_notes_index__g2", "canonical_wiki_index__g2"),
    )
    embedder = Embedder(llm, StaticGenerations((serving, building)))

    query = await embedder.query(["pytanie"])
    assert query.generation is serving
    assert [g.number for g in await embedder.writable()] == [1, 2]
    await embedder.embed_with(building, ["tekst"])

    assert llm.embed_models == ["model-a", "test-embed"]


async def test_no_serving_generation_or_unusable_model_is_not_configured() -> None:
    llm = FakeLLMGateway()
    with pytest.raises(ModelNotConfigured):
        await Embedder(llm, StaticGenerations(())).query(["x"])
    with pytest.raises(ModelNotConfigured):
        await Embedder(llm, StaticGenerations(())).writable()
    gone = StaticGenerations((make_generation(deployment=None),))
    with pytest.raises(ModelNotConfigured):
        await Embedder(llm, gone).query(["x"])
    assert llm.embedded == []  # nothing was embedded with any other model


async def test_vectors_must_have_the_generations_dimension() -> None:
    llm = FakeLLMGateway(embed_dim=16)
    with pytest.raises(EmbeddingMismatch, match="generation 1 has 32"):
        await Embedder(llm, StaticGenerations((make_generation(dim=32),))).embed(["x"])


def test_names_and_keys() -> None:
    assert generation_collection("canonical_wiki_index", 7) == "canonical_wiki_index__g7"
    assert family_of("canonical_wiki_index__g7") == "canonical_wiki_index"
    assert family_of("canonical_wiki_index") == "canonical_wiki_index"
    assert embed_key("http://llm:11434/v1/", "openai", "m") == embed_key(
        "http://llm:11434/v1", "openai", "m"
    )
    assert embed_key("http://a/v1", "openai", "m") != embed_key("http://b/v1", "openai", "m")


async def test_semantic_history_from_another_vector_space_is_ignored() -> None:
    import numpy as np
    from fakeredis import FakeAsyncRedis

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.shield.semantic import SemanticAnomalyDetector, _Entry  # pyright: ignore[reportPrivateUsage]

    detector = SemanticAnomalyDetector(FakeAsyncRedis(), ShieldSettings())
    history = [
        _Entry(1.0, np.ones(8, dtype=np.float32), set()),  # before the model change
        _Entry(2.0, np.ones(4, dtype=np.float32), set()),
        _Entry(3.0, np.ones(4, dtype=np.float32), set()),
    ]
    features = await detector.features(history)
    assert features.queries == 2
