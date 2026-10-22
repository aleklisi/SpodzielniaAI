"""„Rozpoznaj”: what's on a member's photo, grounded in the verified atlas (chunk 9.9).

1. ``RecognitionService.request`` (member, RLS): a PENDING ``RECOGNITION`` derivative of the
   photo, holding the question and the domain (encrypted), queued for worker-core.
2. ``AtlasRecognizer.handle`` (worker-core): the private photo (decrypted locally) is embedded
   whole and as a grid of crops; each crop searches the marks of approved, consented atlas
   photos (mandatory filter, A5) in the photo's domain (or all domains). Labels scoring at least
   ``atlas.recognition_min_score`` become candidates.
   - No candidate → "no verified examples": no model is asked to guess (strict grounding).
   - Otherwise the **local** VLM (``private_data=True``, A8) gets the photo, the candidates'
     vocabulary descriptions and a few verified reference crops, and answers with boxes. Only
     candidate labels are kept, boxes are clamped, and the answer passes the injection guard.
     Without a local VLM the answer falls back to the retrieval (approximate boxes), labelled
     as such.
3. The result (answer, suggested marks, cited atlas photos) is stored encrypted in the
   derivative. It is never indexed and never promoted. Nothing from Qdrant is returned raw
   (A7): only labels, boxes and photo ids that pass ``can_view`` at read time.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, Field
from sqlalchemy import select

from spoldzielnia_ai.atlas.embedding import Box, crop
from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    ConsentStatus,
    DerivativeKind,
    DerivativeStatus,
    VisualDomainStatus,
)
from spoldzielnia_ai.db.models import (
    AnnotatedImage,
    AtlasImage,
    AtlasRegion,
    NoteAttachment,
    NoteDerivative,
    VisualDomain,
)
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.files.preview import render_jpeg
from spoldzielnia_ai.files.validation import FileRejected
from spoldzielnia_ai.llm.gateway import (
    LLMError,
    LLMOutputInvalid,
    LLMPurpose,
    ModelRejected,
    ModelUnavailable,
)
from spoldzielnia_ai.llm.prompts import RECOGNITION
from spoldzielnia_ai.notebook.images import IMAGE_TYPES
from spoldzielnia_ai.notebook.service import ATTACHMENT, DERIVATIVE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL.Image import Image

    from spoldzielnia_ai.atlas.embedding import ImageEmbedder
    from spoldzielnia_ai.atlas.vectors import AtlasHit, AtlasVectorRepo
    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import AtlasSettings
    from spoldzielnia_ai.db.rls import RlsSession
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.storage import ObjectStorage
    from spoldzielnia_ai.llm.gateway import ChatMessage, LLMGateway
    from spoldzielnia_ai.llm.injection_guard import InjectionGuard
    from spoldzielnia_ai.workers.jobs import DerivativeJob

log = get_logger(__name__)

MAX_QUESTION = 300
QUERY_SIDE = 1280
REFERENCE_SIDE = 448
HITS_PER_CROP = 5
NO_EXAMPLES = (
    "W atlasie spółdzielni nie ma zweryfikowanych przykładów podobnych do tego zdjęcia, więc "
    "nie podpowiadam, co na nim jest."
)
FILTERED_ANSWER = "Odpowiedź modelu została odrzucona przez filtr bezpieczeństwa."


def grid() -> list[Box]:
    """Overlapping windows: 2x2 at 60 % and 3x3 at 45 % of each side."""
    boxes: list[Box] = []
    for size, steps in ((0.6, 2), (0.45, 3)):
        stride = (1 - size) / (steps - 1)
        boxes += [
            Box(i * stride, j * stride, size, size) for j in range(steps) for i in range(steps)
        ]
    return boxes


class FoundItem(BaseModel):
    label: str = Field(max_length=100)
    x: float
    y: float
    w: float
    h: float
    confidence: float = 0.5


class RecognitionAnswer(BaseModel):
    answer: str = Field(max_length=2000)
    found: list[FoundItem] = Field(default=[], max_length=20)


@dataclass(frozen=True, slots=True)
class Candidate:
    label_id: UUID
    name: str
    description: str | None
    score: float
    window: Box
    hits: list[AtlasHit] = field(default_factory=lambda: list[Any]())


class RecognitionError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _clamp_box(item: FoundItem) -> Box | None:
    x, y = min(max(item.x, 0.0), 1.0), min(max(item.y, 0.0), 1.0)
    w, h = min(max(item.w, 0.0), 1.0 - x), min(max(item.h, 0.0), 1.0 - y)
    if w < 0.01 or h < 0.01:
        return None
    return Box(x, y, w, h)


class RecognitionService:
    """Member-side: request and read recognitions (RLS)."""

    def __init__(self, session: RlsSession, cipher: EnvelopeCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def request(
        self,
        owner_id: UUID,
        note_id: UUID,
        attachment_id: UUID,
        *,
        question: str | None,
        domain_id: UUID | None,
        all_domains: bool,
    ) -> NoteDerivative:
        attachment = await self._session.scalar(
            select(NoteAttachment).where(
                NoteAttachment.id == attachment_id,
                NoteAttachment.note_id == note_id,
                NoteAttachment.mime_type.in_(IMAGE_TYPES),
            )
        )
        if attachment is None:
            raise RecognitionError("photo_not_found", 404)
        pending = await self._session.scalar(
            select(NoteDerivative.id).where(
                NoteDerivative.attachment_id == attachment.id,
                NoteDerivative.kind == DerivativeKind.RECOGNITION,
                NoteDerivative.status.in_([DerivativeStatus.PENDING, DerivativeStatus.PROCESSING]),
            )
        )
        if pending is not None:
            raise RecognitionError("recognition_in_progress")
        if domain_id is None:
            domain_id = await self._session.scalar(
                select(AnnotatedImage.domain_id).where(
                    AnnotatedImage.attachment_id == attachment.id
                )
            )
        if domain_id is None and not all_domains:
            raise RecognitionError("domain_required", 422)
        if domain_id is not None:
            domain = await self._session.get(VisualDomain, domain_id)
            if domain is None or domain.status != VisualDomainStatus.ACTIVE:
                raise RecognitionError("domain_not_found", 404)
        derivative_id = uuid7()
        aad = row_aad(DERIVATIVE, derivative_id, owner_id)
        key = self._cipher.new_key(aad)
        source = json.dumps(
            {
                "question": (question or "").strip()[:MAX_QUESTION],
                "domain_id": str(domain_id) if domain_id else None,
                "all_domains": all_domains,
            }
        )
        row = NoteDerivative(
            id=derivative_id,
            owner_id=owner_id,
            note_id=attachment.note_id,
            attachment_id=attachment.id,
            kind=DerivativeKind.RECOGNITION,
            status=DerivativeStatus.PENDING,
            source_ciphertext=self._cipher.encrypt_field(key.key, source.encode(), aad, "source"),
            content_ciphertext=None,
            dek_wrapped=key.wrapped,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list(
        self, note_id: UUID, attachment_id: UUID
    ) -> list[tuple[NoteDerivative, dict[str, Any], dict[str, Any] | None]]:
        """Newest first: (row, request, result or None while pending)."""
        rows = await self._session.scalars(
            select(NoteDerivative)
            .where(
                NoteDerivative.attachment_id == attachment_id,
                NoteDerivative.note_id == note_id,
                NoteDerivative.kind == DerivativeKind.RECOGNITION,
            )
            .order_by(NoteDerivative.created_at.desc())
            .limit(10)
        )
        out: list[tuple[NoteDerivative, dict[str, Any], dict[str, Any] | None]] = []
        for row in rows:
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
            request: dict[str, Any] = {}
            if row.source_ciphertext:
                raw = self._cipher.decrypt_field(dek, row.source_ciphertext, aad, "source")
                request = json.loads(raw)
            result: dict[str, Any] | None = None
            if row.content_ciphertext:
                raw = self._cipher.decrypt_field(dek, row.content_ciphertext, aad, "content")
                result = json.loads(raw)
            out.append((row, request, result))
        return out


@dataclass(frozen=True, slots=True)
class _Request:
    owner_id: UUID
    attachment_id: UUID
    object_key: str
    attachment_dek: bytes
    question: str
    domain_id: UUID | None
    all_domains: bool


class AtlasRecognizer:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        storage: ObjectStorage,
        embedder: ImageEmbedder,
        repo: AtlasVectorRepo,
        gateway: LLMGateway,
        guard: InjectionGuard,
        settings: AtlasSettings,
        *,
        max_pixels: int,
        tmp_dir: str | None = None,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._objects = AtlasObjects(cipher, storage)
        self._embedder = embedder
        self._repo = repo
        self._gateway = gateway
        self._guard = guard
        self._settings = settings
        self._max_pixels = max_pixels
        self._tmp_dir = tmp_dir

    async def handle(self, job: DerivativeJob, final_attempt: bool) -> bool:
        """Returns True if the caller should retry later."""
        request = await self._claim(job)
        if request is None:
            return False
        try:
            photo = await self._photo(request)
        except (FileRejected, InvalidTag, OSError):
            await self._finish(job, error="image_unreadable")
            return False
        windows = [Box(0, 0, 1, 1), *grid()]
        crops = [photo if w == Box(0, 0, 1, 1) else crop(photo, w) for w in windows]
        vectors = await asyncio.to_thread(self._embedder.embed, crops)
        candidates = await self._candidates(request, windows, vectors)
        if not candidates:
            await self._finish(
                job,
                result={
                    "answer": NO_EXAMPLES,
                    "grounded": False,
                    "vlm": "not_asked",
                    "suggestions": [],
                    "citations": [],
                },
            )
            return False
        try:
            result = await self._ask(request, photo, candidates)
        except LLMError:
            if not final_attempt:
                return True
            result = self._fallback(candidates, "unavailable")
        await self._finish(job, result=result)
        return False

    async def _claim(self, job: DerivativeJob) -> _Request | None:
        async with rls_session(self._factory, job.owner_id) as session:
            row = await session.get(NoteDerivative, job.derivative_id, with_for_update=True)
            if (
                row is None
                or row.kind != DerivativeKind.RECOGNITION
                or row.status not in (DerivativeStatus.PENDING, DerivativeStatus.PROCESSING)
                or row.attachment_id is None
            ):
                return None
            attachment = await session.get(NoteAttachment, row.attachment_id)
            if attachment is None:
                return None
            row.status = DerivativeStatus.PROCESSING
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
            source = json.loads(
                self._cipher.decrypt_field(dek, row.source_ciphertext or b"{}", aad, "source")
            )
            a_aad = row_aad(ATTACHMENT, attachment.id, attachment.owner_id)
            domain = source.get("domain_id")
            return _Request(
                owner_id=row.owner_id,
                attachment_id=attachment.id,
                object_key=attachment.object_key,
                attachment_dek=self._cipher.unwrap(attachment.dek_wrapped, a_aad),
                question=str(source.get("question") or ""),
                domain_id=UUID(domain) if domain else None,
                all_domains=bool(source.get("all_domains")),
            )

    async def _photo(self, request: _Request) -> Image:
        from PIL import Image

        sealed = io.BytesIO()
        await self._storage.get(request.object_key, sealed)
        sealed.seek(0)
        aad = row_aad(ATTACHMENT, request.attachment_id, request.owner_id)

        def decode() -> Image:
            plain = b"".join(self._cipher.decrypt_stream(request.attachment_dek, sealed, aad))
            with tempfile.NamedTemporaryFile(dir=self._tmp_dir, suffix=".img") as handle:
                handle.write(plain)
                handle.flush()
                rendition = render_jpeg(Path(handle.name), QUERY_SIDE, self._max_pixels)
            with Image.open(io.BytesIO(rendition.data)) as image:
                return image.convert("RGB")

        return await asyncio.to_thread(decode)

    async def _candidates(
        self, request: _Request, windows: list[Box], vectors: list[list[float]]
    ) -> list[Candidate]:
        domain = None if request.all_domains else request.domain_id
        best: dict[UUID, tuple[float, Box, list[AtlasHit]]] = {}
        for window, vector in zip(windows, vectors, strict=True):
            hits = await self._repo.search(
                vector,
                limit=HITS_PER_CROP,
                domain_id=domain,
                kind="region",
                min_score=self._settings.recognition_min_score,
            )
            for hit in hits:
                if hit.label_id is None:
                    continue
                score, box, collected = best.get(hit.label_id, (0.0, window, []))
                collected.append(hit)
                if hit.score > score:
                    score, box = hit.score, window
                best[hit.label_id] = (score, box, collected)
        if not best:
            return []
        ranked = sorted(best.items(), key=lambda item: -item[1][0])
        ranked = ranked[: self._settings.recognition_max_labels]
        async with self._factory() as session:
            labels = await LabelService(session).resolve([label for label, _ in ranked])
        return [
            Candidate(
                label_id=labels[label].id if label in labels else label,
                name=labels[label].name if label in labels else "?",
                description=labels[label].description if label in labels else None,
                score=round(score, 4),
                window=window,
                hits=sorted(hits, key=lambda h: -h.score),
            )
            for label, (score, window, hits) in ranked
        ]

    async def _references(self, candidates: list[Candidate]) -> list[bytes]:
        """One verified crop per candidate (best hit), as small JPEGs."""
        from PIL import Image

        out: list[bytes] = []
        async with self._factory() as session:
            for candidate in candidates[: self._settings.recognition_references]:
                hit = candidate.hits[0]
                if hit.region_id is None:
                    continue
                image = await session.get(AtlasImage, hit.image_id)
                region = await session.get(AtlasRegion, hit.region_id)
                if (
                    image is None
                    or region is None
                    or image.status != AtlasImageStatus.APPROVED
                    or image.consent_status != ConsentStatus.ACTIVE
                ):
                    continue
                data = await self._objects.read(image, "preview")
                if data is None:
                    continue
                with Image.open(io.BytesIO(data)) as source:
                    piece = crop(source.convert("RGB"), Box(region.x, region.y, region.w, region.h))
                piece.thumbnail((REFERENCE_SIDE, REFERENCE_SIDE))
                buffer = io.BytesIO()
                piece.save(buffer, "JPEG", quality=85)
                out.append(buffer.getvalue())
        return out

    async def _ask(
        self, request: _Request, photo: Image, candidates: list[Candidate]
    ) -> dict[str, Any]:
        references = await self._references(candidates)
        listing = "; ".join(f"{c.name}: {c.description or 'brak opisu'}" for c in candidates)
        messages, _ = RECOGNITION.messages(
            [], candidates=listing, question=request.question or "Co widać na zdjęciu?"
        )
        buffer = io.BytesIO()
        photo.save(buffer, "JPEG", quality=85)
        content: list[Any] = [
            {"type": "text", "text": messages[1]["content"]},
            {"type": "image_url", "image_url": {"url": _data_url(buffer.getvalue())}},
        ]
        content += [
            {"type": "image_url", "image_url": {"url": _data_url(ref)}} for ref in references
        ]
        user: ChatMessage = {"role": "user", "content": content}
        try:
            result = await self._gateway.complete(
                [messages[0], user],
                purpose=LLMPurpose.VLM,
                private_data=True,
                response_model=RecognitionAnswer,
            )
        except (ModelUnavailable, ModelRejected):
            return self._fallback(candidates, "unavailable")
        except LLMOutputInvalid:
            return self._fallback(candidates, "invalid")
        parsed = result.parsed
        if parsed is None:
            return self._fallback(candidates, "invalid")
        by_name = {c.name.casefold(): c for c in candidates}
        suggestions: list[dict[str, Any]] = []
        for item in parsed.found:
            candidate = by_name.get(item.label.strip().casefold())
            box = _clamp_box(item)
            if candidate is None or box is None:
                continue  # not a candidate label, or a degenerate box
            suggestions.append(_suggestion(candidate, box, min(max(item.confidence, 0.0), 1.0)))
        answer = parsed.answer.strip()
        verdict = await asyncio.to_thread(self._guard.scan, answer)
        if verdict.quarantine:
            answer = FILTERED_ANSWER
        return {
            "answer": answer,
            "grounded": True,
            "vlm": "ok",
            "suggestions": suggestions,
            "citations": _citations(candidates),
        }

    @staticmethod
    def _fallback(candidates: list[Candidate], vlm: str) -> dict[str, Any]:
        names = ", ".join(f"{c.name} ({c.score:.2f})" for c in candidates)
        return {
            "answer": (
                "Model wizyjny jest niedostępny, więc pokazuję tylko najbardziej podobne "
                f"zweryfikowane oznaczenia: {names}. Ramki są przybliżone."
            ),
            "grounded": True,
            "vlm": vlm,
            "suggestions": [_suggestion(c, c.window, c.score) for c in candidates],
            "citations": _citations(candidates),
        }

    async def _finish(
        self, job: DerivativeJob, *, result: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        async with rls_session(self._factory, job.owner_id) as session:
            row = await session.get(NoteDerivative, job.derivative_id, with_for_update=True)
            if row is None:
                return
            if error is not None:
                row.status = DerivativeStatus.FAILED
                row.error = error
                return
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
            text = json.dumps(result, ensure_ascii=False)
            row.content_ciphertext = self._cipher.encrypt_field(dek, text.encode(), aad, "content")
            row.status = DerivativeStatus.DONE
            row.error = None
        log.info(
            "atlas.recognized",
            derivative=str(job.derivative_id),
            suggestions=len((result or {}).get("suggestions", [])),
            vlm=(result or {}).get("vlm"),
        )


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def _suggestion(candidate: Candidate, box: Box, confidence: float) -> dict[str, Any]:
    return {
        "label_id": str(candidate.label_id),
        "label_name": candidate.name,
        "x": round(box.x, 4),
        "y": round(box.y, 4),
        "w": round(box.w, 4),
        "h": round(box.h, 4),
        "confidence": round(confidence, 3),
    }


def _citations(candidates: Sequence[Candidate]) -> list[dict[str, Any]]:
    """Up to six atlas photos the candidates rest on (best first), with their labels."""
    seen: dict[UUID, dict[str, Any]] = {}
    for candidate in candidates:
        for hit in candidate.hits:
            entry = seen.setdefault(
                hit.image_id, {"image_id": str(hit.image_id), "labels": [], "score": hit.score}
            )
            if candidate.name not in entry["labels"]:
                entry["labels"].append(candidate.name)
            entry["score"] = max(entry["score"], hit.score)
    ranked = sorted(seen.values(), key=lambda e: -e["score"])[:6]
    for entry in ranked:
        entry["score"] = round(entry["score"], 4)
    return ranked
