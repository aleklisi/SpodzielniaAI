"""Chunk 4.1: mandatory filters, scopes, chunking and the embedder (no Qdrant needed)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from qdrant_client import models

from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.vector.chunking import chunk_text
from spoldzielnia_ai.vector.embedding import Embedder, EmbeddingMismatch
from spoldzielnia_ai.vector.repositories import (
    CanonicalPayload,
    CanonicalScope,
    active_canonical_filter,
    private_point_id,
    restricted,
)
from tests.fixtures.llm import FakeLLMGateway, static_generations

MANDATORY = {
    ("consent_status", "ACTIVE"),
    ("is_active", True),
    ("article_status", "CANONICAL"),
}


def _top_level_matches(flt: models.Filter) -> set[tuple[str, object]]:
    assert isinstance(flt.must, list)
    found: set[tuple[str, object]] = set()
    for condition in flt.must:
        if isinstance(condition, models.FieldCondition) and isinstance(
            condition.match, models.MatchValue
        ):
            found.add((condition.key, condition.match.value))
    return found


def test_active_canonical_filter_requires_all_three_conditions() -> None:
    flt = active_canonical_filter()

    assert _top_level_matches(flt) == MANDATORY
    assert flt.should is None
    assert flt.must_not is None


def test_caller_filter_is_anded_never_replacing_the_mandatory_one() -> None:
    hostile = models.Filter(
        must=[
            models.FieldCondition(
                key="consent_status", match=models.MatchValue(value=ConsentStatus.SUSPENDED.value)
            )
        ],
        should=[
            models.FieldCondition(key="is_active", match=models.MatchValue(value=False)),
        ],
    )

    combined = restricted(hostile)

    # The mandatory conditions stay at the top level; the caller's filter is one nested
    # condition next to them, so (SUSPENDED AND ACTIVE) matches nothing.
    assert _top_level_matches(combined) == MANDATORY
    assert isinstance(combined.must, list)
    assert hostile in combined.must
    assert combined.should is None
    assert restricted(None) == active_canonical_filter()


def test_canonical_scope_never_selects_everything() -> None:
    with pytest.raises(ValueError, match="needs author_id"):
        CanonicalScope()
    with pytest.raises(ValueError, match="empty id list"):
        CanonicalScope(author_id=uuid4(), article_ids=())


def test_canonical_scope_round_trips_through_json() -> None:
    scope = CanonicalScope(author_id=uuid4(), article_ids=(uuid4(), uuid4()))

    assert CanonicalScope.from_json(scope.to_json()) == scope
    flt = scope.to_filter()
    assert isinstance(flt.must, list)
    assert len(flt.must) == 2


def test_payload_is_active_only_for_active_consent_in_canonical_article() -> None:
    def payload(consent: ConsentStatus, status: ArticleStatus) -> CanonicalPayload:
        return CanonicalPayload(uuid4(), uuid4(), uuid4(), consent, status, False, None)

    assert payload(ConsentStatus.ACTIVE, ArticleStatus.CANONICAL).is_active
    assert not payload(ConsentStatus.SUSPENDED, ArticleStatus.CANONICAL).is_active
    assert not payload(ConsentStatus.ACTIVE, ArticleStatus.REVISION_REQUIRED).is_active
    data = payload(ConsentStatus.ACTIVE, ArticleStatus.CANONICAL).to_payload()
    assert set(data) == {
        "block_id",
        "article_id",
        "author_id",
        "consent_status",
        "is_active",
        "article_status",
        "is_honeypot",
        "verified_at",
    }  # identifiers and governance metadata only, no text


def test_private_point_ids_are_deterministic() -> None:
    derivative = uuid4()

    assert private_point_id(derivative, 0) == private_point_id(derivative, 0)
    assert private_point_id(derivative, 0) != private_point_id(derivative, 1)


def test_chunks_respect_the_size_limit_and_overlap() -> None:
    paragraphs = [f"Akapit {i} " + "pszczoły " * 40 for i in range(20)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, max_chars=600, overlap=80)

    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)
    assert all("Akapit" in text for text in chunks)
    assert "Akapit 0" in chunks[0]
    assert "Akapit 19" in chunks[-1]


def test_chunking_cuts_a_single_long_paragraph() -> None:
    chunks = chunk_text("słowo " * 2000, max_chars=500, overlap=50)

    assert all(len(c) <= 500 for c in chunks)
    assert sum(len(c) for c in chunks) >= len("słowo " * 2000) - 2000


def test_chunking_short_and_empty_text() -> None:
    assert chunk_text("krótko") == ["krótko"]
    assert chunk_text("") == []


async def test_embedder_batches_and_validates_dimension() -> None:
    gateway = FakeLLMGateway(embed_dim=16)
    embedder = Embedder(gateway, static_generations(16), batch_size=3)

    vectors = await embedder.embed([f"tekst {i}" for i in range(7)])

    assert len(vectors) == 7
    assert all(len(v) == 16 for v in vectors)
    with pytest.raises(EmbeddingMismatch, match="vector size 16"):
        await Embedder(gateway, static_generations(32)).embed(["x"])
