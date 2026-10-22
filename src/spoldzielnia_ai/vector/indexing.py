"""Indexing tasks (Celery queue ``index``, worker-core, 4.1).

``index_private_derivative``: decrypt a scanned, clean derivative as its owner (RLS), chunk,
embed locally, replace the derivative's points in the owner's partition of the private index.

``index_canonical_article``: embed every block of a CANONICAL article that still has content
(ACTIVE and SUSPENDED: a suspended block keeps its point, inactive, so ``resume`` doesn't need
to re-embed) and upsert with the full governance payload. Points of blocks that are gone or
revoked are deleted.

Race with consent and review changes: embedding happens outside any transaction, but the
upsert happens inside one that holds ``FOR SHARE`` locks on the article and its blocks, and the
payload comes from those locked rows. Consent and review changes take ``FOR UPDATE`` on the
same rows and apply their Qdrant operation only after they commit, so they always land after
this upsert, never before it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, DerivativeStatus
from spoldzielnia_ai.db.models import ArticleBlock, NoteDerivative, WikiArticle
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.llm.gateway import LLMError, ModelUnavailable
from spoldzielnia_ai.notebook.service import DERIVATIVE
from spoldzielnia_ai.vector.chunking import chunk_text
from spoldzielnia_ai.vector.repositories import (
    CanonicalPayload,
    CanonicalPoint,
    CanonicalVectorRepo,
    PrivateChunk,
    PrivateVectorRepo,
)

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.vector.embedding import Embedder
    from spoldzielnia_ai.workers.jobs import ArticleJob, DerivativeJob

log = get_logger(__name__)


def canonical_payload(block: ArticleBlock, article: WikiArticle) -> CanonicalPayload:
    """The payload a block's point must carry, from Postgres (the source of truth)."""
    return CanonicalPayload(
        block_id=block.id,
        article_id=article.id,
        author_id=block.author_id,
        consent_status=block.consent_status,
        article_status=article.status,
        is_honeypot=block.is_honeypot,
        verified_at=article.last_verified_at,
    )


def indexable(block: ArticleBlock) -> bool:
    return block.consent_status != ConsentStatus.REVOKED and block.content is not None


class Indexer:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        embedder: Embedder,
        client: AsyncQdrantClient,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._embedder = embedder
        self._client = client

    async def index_private_derivative(self, job: DerivativeJob, final_attempt: bool) -> bool:
        """Returns True if the caller should retry later."""
        async with rls_session(self._factory, job.owner_id) as session:
            row = await session.get(NoteDerivative, job.derivative_id)
            # Quarantined derivatives stay out of the notebook index, confirmed or not.
            if row is None or row.status != DerivativeStatus.DONE or row.content_ciphertext is None:
                return False
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
            text = self._cipher.decrypt_field(dek, row.content_ciphertext, aad, "content").decode()
            note_id = row.note_id

        chunks = chunk_text(text)
        try:
            vectors = await self._embedder.embed(chunks)
        except ModelUnavailable:
            return not final_attempt
        except LLMError as exc:
            log.error("index.private_failed", derivative=str(job.derivative_id), error=str(exc))
            return False

        repo = PrivateVectorRepo(self._client, job.owner_id)
        # The note may have been deleted meanwhile: re-check, then replace its points.
        async with rls_session(self._factory, job.owner_id) as session:
            if await session.get(NoteDerivative, job.derivative_id, with_for_update=True) is None:
                return False
            await repo.delete_derivative(job.derivative_id)
            await repo.upsert(
                [
                    PrivateChunk(note_id, job.derivative_id, i, vector)
                    for i, vector in enumerate(vectors)
                ]
            )
        log.info("index.private_done", derivative=str(job.derivative_id), chunks=len(chunks))
        return False

    async def index_canonical_article(self, job: ArticleJob, final_attempt: bool) -> bool:
        """Returns True if the caller should retry later."""
        async with self._factory() as session:
            article = await session.get(WikiArticle, job.article_id)
            if article is None or article.status != ArticleStatus.CANONICAL:
                return False
            blocks = list(
                await session.scalars(
                    select(ArticleBlock).where(ArticleBlock.article_id == job.article_id)
                )
            )
        snapshot = {b.id: b.content for b in blocks if indexable(b) and b.content is not None}
        ids = list(snapshot)
        try:
            embedded = await self._embedder.embed([snapshot[i] for i in ids])
        except ModelUnavailable:
            return not final_attempt
        except LLMError as exc:
            log.error("index.canonical_failed", article=str(job.article_id), error=str(exc))
            return False
        vectors = dict(zip(ids, embedded, strict=True))

        repo = CanonicalVectorRepo(self._client)
        async with self._factory() as session, session.begin():
            article = await session.get(WikiArticle, job.article_id, with_for_update={"read": True})
            if article is None or article.status != ArticleStatus.CANONICAL:
                return False  # left CANONICAL meanwhile: its own payload update handles it
            locked = list(
                await session.scalars(
                    select(ArticleBlock)
                    .where(ArticleBlock.article_id == job.article_id)
                    .with_for_update(read=True)
                )
            )
            points: list[CanonicalPoint] = []
            stale = False
            for block in locked:
                if not indexable(block):
                    continue
                if block.id not in vectors or block.content != snapshot[block.id]:
                    stale = True  # edited or added since the snapshot
                    continue
                points.append(CanonicalPoint(canonical_payload(block, article), vectors[block.id]))
            await repo.upsert(points)
            await repo.delete_article_except(job.article_id, [p.payload.block_id for p in points])
        log.info("index.canonical_done", article=str(job.article_id), points=len(points))
        return stale and not final_attempt
