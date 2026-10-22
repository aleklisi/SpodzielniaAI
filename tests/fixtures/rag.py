"""FakeLLM replies for answer synthesis (Stage 5), and a RAG wiring over real Postgres/Qdrant."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from sqlalchemy import func, update

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.settings import RagSettings
from spoldzielnia_ai.db.enums import ReviewDecision, Role
from spoldzielnia_ai.db.models import ArticleBlock, ExpertReview, User, WikiArticle
from spoldzielnia_ai.rag.service import RAGService
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.workers.jobs import ArticleJob
from tests.fixtures.llm import FakeLLMGateway, static_generations
from tests.fixtures.wiki import make_article, make_user

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.db.session import SessionFactory
    from tests.fixtures.llm import Call

_LABEL = re.compile(
    r'<untrusted_data id="[0-9a-f]+" label="(S\d+)">\n(.*?)\n</untrusted_data', re.S
)
_WORD = re.compile(r"\w+")
# Canonical indexing never decrypts anything.
_NO_CIPHER = EnvelopeCipher(StaticKeyProvider("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="))


def sources(call: Call) -> list[tuple[str, str]]:
    """(label, text) of every source the model was given."""
    return _LABEL.findall(call.text)


def draft(answer: str, citations: list[str], insufficient: bool = False) -> str:
    return json.dumps(
        {"answer_markdown": answer, "citations": citations, "insufficient": insufficient},
        ensure_ascii=False,
    )


def citing(*labels: str, answer: str = "Należy leczyć rodziny zimą") -> Callable[[Call], str]:
    """Always cites ``labels``, whatever the sources were."""

    def reply(_call: Call) -> str:
        marks = "".join(f"[{label}]" for label in labels)
        return draft(f"{answer} {marks}.", list(labels))

    return reply


def paraphrase(call: Call) -> str:
    """A grounded paraphrase: per source, a few of its words reordered and rephrased."""
    found = sources(call)
    if not found:
        return draft("", [], insufficient=True)
    sentences: list[str] = []
    for label, text in found[:3]:
        words = _WORD.findall(text)[:4]
        sentences.append(f"Źródło omawia kwestię: {', '.join(reversed(words))} [{label}].")
    return draft(" ".join(sentences), [label for label, _ in found[:3]])


def verbatim(call: Call) -> str:
    """Copies every source word for word (the extraction attack's best case)."""
    found = sources(call)
    body = "\n\n".join(f"{text} [{label}]" for label, text in found)
    return draft(body, [label for label, _ in found])


def rag_service(
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    llm: FakeLLMGateway | None = None,
    **settings: object,
) -> tuple[RAGService, FakeLLMGateway]:
    llm = llm or FakeLLMGateway(reply=paraphrase)
    config = RagSettings.model_validate({"score_threshold": 0.3, **settings})
    service = RAGService(
        factory,
        qdrant,
        Embedder(llm, static_generations()),
        llm,
        config,
    )
    return service, llm


async def canonical(
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    blocks: list[tuple[UUID, str]],
    *,
    title: str = "Leczenie warrozy",
    expert_id: UUID | None = None,
    honeypot: list[bool] | None = None,
    llm: FakeLLMGateway | None = None,
) -> tuple[UUID, list[UUID]]:
    """A CANONICAL, verified article (approved by ``expert_id`` if given), indexed in Qdrant."""
    article_id, block_ids = await make_article(factory, blocks, title=title)
    async with factory() as session, session.begin():
        verified = update(WikiArticle).where(WikiArticle.id == article_id)
        await session.execute(verified.values(last_verified_at=func.now()))
        if honeypot:
            for block_id, flag in zip(block_ids, honeypot, strict=True):
                await session.execute(
                    update(ArticleBlock).where(ArticleBlock.id == block_id).values(is_honeypot=flag)
                )
        if expert_id is not None:
            session.add(
                ExpertReview(
                    article_id=article_id, reviewer_id=expert_id, decision=ReviewDecision.APPROVE
                )
            )
    embedder = Embedder(llm or FakeLLMGateway(), static_generations())
    indexer = Indexer(factory, _NO_CIPHER, embedder, qdrant)
    await indexer.index_canonical_article(ArticleJob(article_id=article_id), True)
    return article_id, block_ids


async def named_user(factory: SessionFactory, name: str, *roles: Role) -> UUID:
    user_id = await make_user(factory, *roles)
    async with factory() as session, session.begin():
        await session.execute(update(User).where(User.id == user_id).values(display_name=name))
    return user_id
