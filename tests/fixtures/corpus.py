"""A synthetic, seeded canonical corpus: 40 articles ("rozdziały") x 5 blocks (Stage 5.6).

Each article has its own vocabulary of invented technical terms. ``TopicEmbedding`` is a toy
semantic model: every term of an article points in that article's topic direction, other words
add a little hashed noise. So two questions about one topic are close even when they share
few exact words (as with a real embedding model), and different topics are far apart. Blocks
and questions are embedded the same way. Titles are public (``/wiki/articles``): an extraction
bot can walk them.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import SecretStr

from spoldzielnia_ai.core.settings import QdrantSettings, ShieldSettings
from spoldzielnia_ai.shield.clusters import refresh_clusters
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from tests.fixtures.llm import EMBED_DIM, FakeLLMGateway, hash_embedding
from tests.fixtures.rag import canonical, named_user, paraphrase

if TYPE_CHECKING:
    from uuid import UUID

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory
    from tests.fixtures.app import Harness

ARTICLES, BLOCKS = 40, 5
_SYLLABLES = "ka ro mi ul pe za no wi ta se lu bo de fi gra pro ste kre".split()  # noqa: SIM905
_FILLER = (  # noqa: SIM905 - one readable sentence
    "w pasiece należy obserwować rodzinę i notować wyniki przeglądu oraz reagować na zmiany "
    "pogody pożytku i siły rodziny pszczelej przez cały sezon"
).split()


@dataclass(frozen=True, slots=True)
class Chapter:
    number: int
    title: str
    terms: list[str]
    blocks: list[str]
    article_id: UUID | None = None


def chapters(seed: int = 2026) -> list[Chapter]:
    rng = random.Random(seed)
    used: set[str] = set()
    out: list[Chapter] = []
    for number in range(1, ARTICLES + 1):
        terms: list[str] = []
        while len(terms) < 6:
            word = "".join(rng.choice(_SYLLABLES) for _ in range(3))
            if word not in used:
                used.add(word)
                terms.append(word)
        blocks: list[str] = []
        for b in range(BLOCKS):
            own = rng.sample(terms, 4)
            filler = rng.sample(_FILLER, 5)
            blocks.append(f"Część {b + 1}. " + " ".join(own[:2] + filler + own[2:]) + ".")
        title = f"Rozdział {number}: {terms[0]} i {terms[1]}"
        out.append(Chapter(number, title, terms, blocks))
    return out


_TOKEN = re.compile(r"\w+")
GENERIC_WEIGHT = 0.3


class TopicEmbedding:
    def __init__(self, corpus: list[Chapter], seed: int = 7) -> None:
        rng = random.Random(seed)
        self.topic_of: dict[str, list[float]] = {}
        for chapter in corpus:
            direction = [rng.gauss(0, 1) for _ in range(EMBED_DIM)]
            norm = math.sqrt(sum(x * x for x in direction))
            for term in chapter.terms:
                self.topic_of[term] = [x / norm for x in direction]

    def __call__(self, text: str) -> list[float]:
        vector = [0.0] * EMBED_DIM
        for token in _TOKEN.findall(text.lower()):
            topic = self.topic_of.get(token)
            part = topic if topic is not None else hash_embedding(token)
            weight = 1.0 if topic is not None else GENERIC_WEIGHT
            vector = [v + weight * p for v, p in zip(vector, part, strict=True)]
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


async def seed_corpus(
    factory: SessionFactory, qdrant: AsyncQdrantClient, embedding: TopicEmbedding
) -> list[Chapter]:
    author = await named_user(factory, "Anna Kowalska")
    seeded: list[Chapter] = []
    for chapter in chapters():
        article_id, _ = await canonical(
            factory,
            qdrant,
            [(author, text) for text in chapter.blocks],
            title=chapter.title,
            llm=FakeLLMGateway(embedding=embedding),
        )
        seeded.append(
            Chapter(chapter.number, chapter.title, chapter.terms, chapter.blocks, article_id)
        )
    return seeded


@dataclass
class Corpus:
    chapters: list[Chapter]
    embedding: TopicEmbedding

    def llm(self) -> FakeLLMGateway:
        return FakeLLMGateway(reply=paraphrase, embedding=self.embedding)

    def walk(self, n: int) -> str:
        """The bot's n-th question: the next chapter of the public table of contents."""
        return f"Opisz rozdział {n}: {' '.join(self.chapters[(n - 1) % ARTICLES].terms)}"


async def prepared_corpus(h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient) -> Corpus:
    """The corpus, indexed, with the topic clusters the nightly task (5.3) would compute."""
    embedding = TopicEmbedding(chapters())
    seeded = await seed_corpus(factory, qdrant, embedding)
    assert await refresh_clusters(CanonicalVectorRepo(qdrant), h.app.state.redis, 20) == 20
    return Corpus(seeded, embedding)


def shield_settings(settings: Settings, **shield: object) -> Settings:
    """Defaults, with the keys set, no jittered delay (real latency the tests would wait for)
    and an 8-bit proof-of-work (solved in Python by the tests)."""
    config = ShieldSettings.model_validate(
        {
            "honeypot_key": SecretStr("stage5-test-honeypot-key"),
            "watermark_key": SecretStr("stage5-test-watermark-key"),
            "elevated_delay_ms": (0, 0),
            "pow_difficulty_bits": 8,
            **shield,
        }
    )
    return settings.model_copy(
        update={"qdrant": QdrantSettings(vector_size=EMBED_DIM), "shield": config}
    )
