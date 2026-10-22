"""Push-to-wiki (chunk 3.5): private notes → scrubbed, screened, locally generated wiki draft.

API side (``request_promotion``): all sources must be readable through RLS (404 otherwise).
Derivatives must be finished, and QUARANTINED ones confirmed by their owner (409). A private
``promotion`` row stores the source IDs and title hint encrypted.

Worker side (``PromotionProcessor``, worker-core, queue ``promotion``):

1. Decrypt the sources (RLS as the owner).
2. **PII scrub** all sources with one pseudonym table (Presidio + checksum IDs).
3. **Injection screen** each scrubbed source, plus the parsing findings of derivatives (hidden
   text …). A source above the threshold that its owner did not confirm stops the promotion.
4. Local LLM (``PROMOTION``, ``private_data=True``) with the safe prompt → ``ArticleDraft``.
5. **Post-LLM PII re-scan** (the model may reconstruct identifiers) → scrub again, count.
6. ``WikiArticle(DRAFT)`` + ``ArticleBlock``s with ``author_id`` = owner and
   ``consent_status=ACTIVE``, and ``NOTE_PROMOTED`` audit (prompt digest, model, counts).

Nothing derived from the sources' content enters ``injection_flags``, ``pii_stats`` or the
audit log: only kinds, counts and scores.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    ArticleStatus,
    ConsentStatus,
    DerivativeKind,
    DerivativeStatus,
    PromotionStatus,
)
from spoldzielnia_ai.db.models import (
    ArticleBlock,
    NoteDerivative,
    PrivateNote,
    Promotion,
    WikiArticle,
)
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.llm.gateway import (
    LLMError,
    LLMOutputInvalid,
    LLMPurpose,
    ModelRejected,
    ModelUnavailable,
)
from spoldzielnia_ai.llm.injection_guard import document_flags
from spoldzielnia_ai.llm.prompts import PROMOTION, restore_placeholders
from spoldzielnia_ai.notebook.service import DERIVATIVE, NOTE

if TYPE_CHECKING:
    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import LLMGateway
    from spoldzielnia_ai.llm.injection_guard import InjectionGuard
    from spoldzielnia_ai.privacy.scrubber import PiiScrubber
    from spoldzielnia_ai.workers.jobs import PromotionJob

log = get_logger(__name__)

TABLE = "promotion"
MAX_SOURCES = 20
_KIND_LABEL = {
    DerivativeKind.TRANSCRIPT: "transkrypcja nagrania",
    DerivativeKind.IMAGE_DESCRIPTION: "opis zdjęcia",
    DerivativeKind.PARSED_DOC: "dokument",
    DerivativeKind.FETCHED_LINK: "strona internetowa",
    DerivativeKind.ANNOTATIONS: "oznaczenia zdjęcia",
}


class DraftBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str | None = Field(default=None, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class ArticleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    blocks: list[DraftBlock] = Field(min_length=1, max_length=100)


class PromotionRejected(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def source_ids_hash(note_ids: list[UUID], derivative_ids: list[UUID]) -> str:
    material = ",".join(
        sorted(f"n:{n}" for n in note_ids) + sorted(f"d:{d}" for d in derivative_ids)
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _usable(derivative: NoteDerivative) -> str | None:
    """None if the derivative may be promoted, else the reason it may not."""
    if derivative.kind not in _KIND_LABEL:  # e.g. a recognition result: model output, not notes
        return "source_not_promotable"
    if derivative.status == DerivativeStatus.DONE:
        return None
    if derivative.status == DerivativeStatus.QUARANTINED:
        return None if derivative.quarantine_confirmed_at is not None else "source_quarantined"
    return "source_not_ready"


async def request_promotion(
    factory: SessionFactory,
    cipher: EnvelopeCipher,
    owner_id: UUID,
    note_ids: list[UUID],
    derivative_ids: list[UUID],
    title_hint: str | None,
) -> tuple[UUID, UUID]:
    """Validate the sources and create the private promotion row. Returns (promotion, article)."""
    async with rls_session(factory, owner_id) as session:
        for note_id in note_ids:
            if await session.get(PrivateNote, note_id) is None:
                raise PromotionRejected("source_not_found", 404)
        for derivative_id in derivative_ids:
            derivative = await session.get(NoteDerivative, derivative_id)
            if derivative is None:
                raise PromotionRejected("source_not_found", 404)
            if (reason := _usable(derivative)) is not None:
                raise PromotionRejected(reason, 409)

        promotion_id, article_id = uuid7(), uuid7()
        aad = row_aad(TABLE, promotion_id, owner_id)
        key = cipher.new_key(aad)
        sources = json.dumps(
            {"notes": [str(n) for n in note_ids], "derivatives": [str(d) for d in derivative_ids]}
        )
        session.add(
            Promotion(
                id=promotion_id,
                owner_id=owner_id,
                status=PromotionStatus.PENDING,
                article_id=article_id,
                sources_ciphertext=cipher.encrypt_field(key.key, sources.encode(), aad, "sources"),
                title_hint_ciphertext=(
                    cipher.encrypt_field(key.key, title_hint.encode(), aad, "title_hint")
                    if title_hint
                    else None
                ),
                dek_wrapped=key.wrapped,
                source_ids_hash=source_ids_hash(note_ids, derivative_ids),
                source_count=len(note_ids) + len(derivative_ids),
            )
        )
    return promotion_id, article_id


@dataclass(frozen=True, slots=True)
class _Source:
    label: str
    text: str
    stored_flags: dict[str, Any]
    confirmed: bool  # a quarantined derivative its owner explicitly allowed


@dataclass(frozen=True, slots=True)
class _Loaded:
    article_id: UUID
    sources: list[_Source]
    title_hint: str | None


class PromotionProcessor:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        scrubber: PiiScrubber,
        guard: InjectionGuard,
        gateway: LLMGateway,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._scrubber = scrubber
        self._guard = guard
        self._gateway = gateway

    async def handle(self, job: PromotionJob, final_attempt: bool) -> bool:
        """Process ``job``. Returns True if the caller should retry later."""
        try:
            loaded = await self._load(job)
        except PromotionRejected as exc:
            await self._fail(job, exc.code)
            return False
        if loaded is None:
            return False

        # 2. PII: one pseudonym table for all sources (and the title hint).
        texts = [s.text for s in loaded.sources] + [loaded.title_hint or ""]
        scrubbed = await asyncio.to_thread(self._scrubber.scrub_many, texts)
        pii = Counter[str]()
        for result in scrubbed:
            pii.update(result.stats)
        documents = [(s.label, r.text) for s, r in zip(loaded.sources, scrubbed, strict=False)]
        title_hint = scrubbed[-1].text.strip() or "brak"

        # 3. Injection screening on what the model would see.
        flags: list[dict[str, JsonValue]] = []
        kinds: set[str] = set()
        for index, (source, (_, text)) in enumerate(zip(loaded.sources, documents, strict=True)):
            verdict = await asyncio.to_thread(
                self._guard.scan, text, document_flags(source.stored_flags)
            )
            kinds.update(verdict.kinds())
            flags.append(
                {
                    "source": index,
                    "score": round(verdict.score, 4),
                    "kinds": list[JsonValue](verdict.kinds()),
                    "confirmed": source.confirmed,
                }
            )
            if verdict.quarantine and not source.confirmed:
                await self._fail(
                    job, "injection_detected", {"sources": flags, "kinds": sorted(kinds)}
                )
                return False
        injection = {"sources": flags, "kinds": sorted(kinds)}

        # 4. Local LLM.
        messages, _ = PROMOTION.messages(documents, title_hint=title_hint)
        try:
            result = await self._gateway.complete(
                messages,
                purpose=LLMPurpose.PROMOTION,
                private_data=True,
                response_model=ArticleDraft,
            )
        except ModelUnavailable:
            if final_attempt:
                await self._fail(job, "model_unavailable", injection)
                return False
            return True
        except ModelRejected:  # e.g. the server can't do JSON-schema output for this model
            await self._fail(job, "model_rejected", injection)
            return False
        except LLMOutputInvalid:
            await self._fail(job, "llm_output_invalid", injection)
            return False
        except LLMError:
            if final_attempt:
                await self._fail(job, "llm_error", injection)
                return False
            return True
        draft = result.parsed
        assert draft is not None  # noqa: S101 - response_model was given

        # 5. The model may have reconstructed PII: scrub its output again.
        outputs = (
            [draft.title] + [b.heading or "" for b in draft.blocks] + [b.text for b in draft.blocks]
        )
        outputs = [restore_placeholders(o) for o in outputs]
        rescrubbed = await asyncio.to_thread(self._scrubber.scrub_many, outputs)
        after = Counter[str]()
        for r in rescrubbed:
            after.update(r.stats)
        n = len(draft.blocks)
        title = rescrubbed[0].text.strip() or "Szkic artykułu"
        headings = [r.text.strip() or None for r in rescrubbed[1 : 1 + n]]
        bodies = [r.text for r in rescrubbed[1 + n :]]

        await self._complete(
            job,
            loaded.article_id,
            title,
            list(zip(headings, bodies, strict=True)),
            model=result.model,
            injection=injection,
            pii={"sources": dict(pii), "after_llm": dict(after)},
        )
        return False

    async def _load(self, job: PromotionJob) -> _Loaded | None:
        async with rls_session(self._factory, job.owner_id) as session:
            promotion = await session.get(Promotion, job.promotion_id, with_for_update=True)
            if promotion is None or promotion.status not in (
                PromotionStatus.PENDING,
                PromotionStatus.RUNNING,
            ):
                return None
            promotion.status = PromotionStatus.RUNNING
            aad = row_aad(TABLE, promotion.id, promotion.owner_id)
            dek = self._cipher.unwrap(promotion.dek_wrapped, aad)
            ids = json.loads(
                self._cipher.decrypt_field(dek, promotion.sources_ciphertext, aad, "sources")
            )
            hint = (
                self._cipher.decrypt_field(
                    dek, promotion.title_hint_ciphertext, aad, "title_hint"
                ).decode()
                if promotion.title_hint_ciphertext
                else None
            )
            sources: list[_Source] = []
            for i, note_id in enumerate(ids["notes"], start=1):
                note = await session.get(PrivateNote, UUID(note_id))
                if note is None:
                    raise PromotionRejected("source_not_found", 404)
                note_aad = row_aad(NOTE, note.id, note.owner_id)
                note_dek = self._cipher.unwrap(note.dek_wrapped, note_aad)
                title = self._cipher.decrypt_field(
                    note_dek, note.title_ciphertext, note_aad, "title"
                )
                body = self._cipher.decrypt_field(note_dek, note.body_ciphertext, note_aad, "body")
                text = f"{title.decode()}\n\n{body.decode()}".strip()
                sources.append(_Source(f"notatka {i}", text, {}, confirmed=False))
            for i, derivative_id in enumerate(ids["derivatives"], start=1):
                derivative = await session.get(NoteDerivative, UUID(derivative_id))
                if derivative is None or derivative.content_ciphertext is None:
                    raise PromotionRejected("source_not_found", 404)
                if (reason := _usable(derivative)) is not None:
                    raise PromotionRejected(reason, 409)
                d_aad = row_aad(DERIVATIVE, derivative.id, derivative.owner_id)
                d_dek = self._cipher.unwrap(derivative.dek_wrapped, d_aad)
                content = self._cipher.decrypt_field(
                    d_dek, derivative.content_ciphertext, d_aad, "content"
                )
                sources.append(
                    _Source(
                        f"{_KIND_LABEL[derivative.kind]} {i}",
                        content.decode(),
                        dict(derivative.injection_flags),
                        confirmed=derivative.quarantine_confirmed_at is not None,
                    )
                )
            return _Loaded(promotion.article_id, sources, hint)

    async def _complete(
        self,
        job: PromotionJob,
        article_id: UUID,
        title: str,
        blocks: list[tuple[str | None, str]],
        *,
        model: str,
        injection: dict[str, Any],
        pii: dict[str, Any],
    ) -> None:
        async with rls_session(self._factory, job.owner_id) as session:
            promotion = await session.get(Promotion, job.promotion_id, with_for_update=True)
            if promotion is None or promotion.status != PromotionStatus.RUNNING:
                return
            session.add(
                WikiArticle(
                    id=article_id,
                    title=title[:200],
                    status=ArticleStatus.DRAFT,
                    created_from_promotion_id=promotion.id,
                )
            )
            await session.flush()
            for position, (heading, text) in enumerate(blocks):
                content = f"## {heading}\n\n{text}" if heading else text
                session.add(
                    ArticleBlock(
                        article_id=article_id,
                        position=position,
                        author_id=job.owner_id,
                        consent_status=ConsentStatus.ACTIVE,
                        content=content,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    )
                )
            promotion.status = PromotionStatus.DONE
            promotion.error = None
            promotion.prompt_name = PROMOTION.name
            promotion.prompt_version = PROMOTION.version
            promotion.prompt_digest = PROMOTION.digest
            promotion.model = model[:200]
            promotion.injection_flags = injection
            promotion.pii_stats = pii
            payload: dict[str, JsonValue] = {
                "promotion": str(promotion.id),
                "article": str(article_id),
                "blocks": len(blocks),
                "sources": promotion.source_count,
                "prompt_digest": PROMOTION.digest,
                "model": model[:200],
                "pii_after_llm": sum(pii["after_llm"].values()),
                "injection_kinds": list[JsonValue](injection.get("kinds", [])),
            }
            await AuditService(session).record(
                EventType.NOTE_PROMOTED,
                job.owner_id,
                AuditTarget("wiki_article", str(article_id)),
                payload,
            )
        log.info("promotion.done", promotion=str(job.promotion_id), blocks=len(blocks))

    async def _fail(
        self, job: PromotionJob, code: str, injection: dict[str, Any] | None = None
    ) -> None:
        async with rls_session(self._factory, job.owner_id) as session:
            promotion = await session.get(Promotion, job.promotion_id, with_for_update=True)
            if promotion is None:
                return
            promotion.status = PromotionStatus.FAILED
            promotion.error = code[:64]
            if injection is not None:
                promotion.injection_flags = injection
            await AuditService(session).record(
                EventType.PROMOTION_FAILED,
                job.owner_id,
                AuditTarget(TABLE, str(job.promotion_id)),
                {"error": code[:64]},
            )
        log.warning("promotion.failed", promotion=str(job.promotion_id), error=code)
