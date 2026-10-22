"""The only access paths to the two Qdrant collections (4.1).

- ``PrivateVectorRepo(user_id)``: every search, upsert and delete is scoped to one tenant. There
  is no method without it (gap G9): the Qdrant equivalent of RLS.
- ``CanonicalVectorRepo``: every search ANDs ``active_canonical_filter()``. Callers may narrow
  the filter, never widen it. Writes take a ``CanonicalScope`` that can't select the whole
  collection by accident.

Point IDs: a canonical point's ID is its ``block_id``. A private point's ID is derived from
``(derivative_id, chunk)``, so re-indexing overwrites instead of duplicating.

Each repository works on one collection: one index generation's (10.3). Writes that must reach
every live generation (consent, governance, deletions) go through ``CanonicalFanout`` and
``PrivateFanout``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from qdrant_client import models

from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping
    from datetime import datetime

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.vector.generations import GenerationSource

_PRIVATE_POINT_NS = UUID("6f0c51a4-3c6a-4a43-9f39-0d7c1c0e9a11")
SCROLL_BATCH = 256


def _match(key: str, value: str | bool) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _match_any(key: str, values: Iterable[UUID]) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchAny(any=[str(v) for v in values]))


def _has_id(ids: Iterable[UUID]) -> models.HasIdCondition:
    point_ids: list[models.ExtendedPointId] = [str(i) for i in ids]
    return models.HasIdCondition(has_id=point_ids)


# --- Private (per tenant) -----------------------------------------------------------------------


def private_point_id(derivative_id: UUID, chunk: int) -> UUID:
    return uuid.uuid5(_PRIVATE_POINT_NS, f"{derivative_id}:{chunk}")


@dataclass(frozen=True, slots=True)
class PrivateChunk:
    note_id: UUID
    derivative_id: UUID
    chunk: int
    vector: list[float]


@dataclass(frozen=True, slots=True)
class PrivateHit:
    note_id: UUID
    derivative_id: UUID
    chunk: int
    score: float


class PrivateVectorRepo:
    def __init__(self, client: AsyncQdrantClient, user_id: UUID, collection: str) -> None:
        self._client = client
        self._user_id = user_id
        self._collection = collection

    def _tenant(self, *conditions: models.Condition) -> models.Filter:
        return models.Filter(must=[_match("user_id", str(self._user_id)), *conditions])

    async def upsert(self, chunks: list[PrivateChunk]) -> None:
        if not chunks:
            return
        points = [
            models.PointStruct(
                id=str(private_point_id(c.derivative_id, c.chunk)),
                vector=c.vector,
                payload={
                    "user_id": str(self._user_id),
                    "note_id": str(c.note_id),
                    "derivative_id": str(c.derivative_id),
                    "chunk": c.chunk,
                },
            )
            for c in chunks
        ]
        await self._client.upsert(self._collection, points=points, wait=True)

    async def search(self, vector: list[float], limit: int = 8) -> list[PrivateHit]:
        response = await self._client.query_points(
            self._collection,
            query=vector,
            query_filter=self._tenant(),
            limit=limit,
            with_payload=True,
        )
        hits: list[PrivateHit] = []
        for point in response.points:
            payload = point.payload or {}
            if payload.get("user_id") != str(self._user_id):  # never, unless the filter broke
                continue
            hits.append(
                PrivateHit(
                    note_id=UUID(str(payload["note_id"])),
                    derivative_id=UUID(str(payload["derivative_id"])),
                    chunk=int(payload["chunk"]),
                    score=point.score,
                )
            )
        return hits

    async def delete_derivative(self, derivative_id: UUID) -> None:
        selector = models.FilterSelector(
            filter=self._tenant(_match("derivative_id", str(derivative_id)))
        )
        await self._client.delete(self._collection, points_selector=selector, wait=True)

    async def delete_note(self, note_id: UUID) -> None:
        selector = models.FilterSelector(filter=self._tenant(_match("note_id", str(note_id))))
        await self._client.delete(self._collection, points_selector=selector, wait=True)


# --- Canonical (shared) -------------------------------------------------------------------------


def _active_conditions() -> list[models.Condition]:
    return [
        _match("consent_status", ConsentStatus.ACTIVE.value),
        _match("is_active", True),
        _match("article_status", ArticleStatus.CANONICAL.value),
    ]


def active_canonical_filter() -> models.Filter:
    """Only consent-active blocks of canonical articles. The one definition of "retrievable"."""
    return models.Filter(must=_active_conditions())


def restricted(extra: models.Filter | None) -> models.Filter:
    """``active_canonical_filter() AND extra``. ``extra`` is nested as one condition of the
    mandatory filter, so nothing in it (``should``, its own ``consent_status`` …) can replace or
    loosen the mandatory conditions."""
    if extra is None:
        return active_canonical_filter()
    return models.Filter(must=[*_active_conditions(), extra])


@dataclass(frozen=True, slots=True)
class CanonicalPayload:
    block_id: UUID
    article_id: UUID
    author_id: UUID
    consent_status: ConsentStatus
    article_status: ArticleStatus
    is_honeypot: bool
    verified_at: datetime | None

    @property
    def is_active(self) -> bool:
        return (
            self.consent_status == ConsentStatus.ACTIVE
            and self.article_status == ArticleStatus.CANONICAL
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "block_id": str(self.block_id),
            "article_id": str(self.article_id),
            "author_id": str(self.author_id),
            "consent_status": self.consent_status.value,
            "is_active": self.is_active,
            "article_status": self.article_status.value,
            "is_honeypot": self.is_honeypot,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


@dataclass(frozen=True, slots=True)
class CanonicalScope:
    """Which canonical points a write applies to. At least one field must be set: no write ever
    selects the whole collection. Fields combine with AND. JSON-serialisable (outbox, 4.4)."""

    author_id: UUID | None = None
    article_ids: tuple[UUID, ...] | None = None
    block_ids: tuple[UUID, ...] | None = None

    def __post_init__(self) -> None:
        if self.author_id is None and self.article_ids is None and self.block_ids is None:
            raise ValueError("a canonical scope needs author_id, article_ids or block_ids")
        if self.article_ids == () or self.block_ids == ():
            raise ValueError("empty id list in a canonical scope")

    def to_filter(self) -> models.Filter:
        must: list[models.Condition] = []
        if self.author_id is not None:
            must.append(_match("author_id", str(self.author_id)))
        if self.article_ids is not None:
            must.append(_match_any("article_id", self.article_ids))
        if self.block_ids is not None:
            must.append(_has_id(self.block_ids))
        return models.Filter(must=must)

    def to_json(self) -> dict[str, Any]:
        return {
            "author_id": str(self.author_id) if self.author_id else None,
            "article_ids": [str(a) for a in self.article_ids] if self.article_ids else None,
            "block_ids": [str(b) for b in self.block_ids] if self.block_ids else None,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> CanonicalScope:
        def ids(key: str) -> tuple[UUID, ...] | None:
            values = data.get(key)
            return tuple(UUID(str(v)) for v in values) if values is not None else None

        author = data.get("author_id")
        return cls(
            author_id=UUID(str(author)) if author else None,
            article_ids=ids("article_ids"),
            block_ids=ids("block_ids"),
        )


def _dense(vector: models.VectorStructOutput | None) -> list[float]:
    if isinstance(vector, list) and vector and not isinstance(vector[0], list):
        return [float(x) for x in cast("list[float]", vector)]
    raise ValueError("canonical points hold one dense vector")


@dataclass(frozen=True, slots=True)
class CanonicalPoint:
    payload: CanonicalPayload
    vector: list[float]


@dataclass(frozen=True, slots=True)
class CanonicalHit:
    block_id: UUID
    article_id: UUID
    author_id: UUID
    is_honeypot: bool
    score: float


@dataclass(frozen=True, slots=True)
class StoredPoint:
    id: UUID
    payload: dict[str, Any]


class CanonicalVectorRepo:
    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self._client = client
        self.collection = collection

    async def upsert(self, points: list[CanonicalPoint]) -> None:
        if not points:
            return
        structs = [
            models.PointStruct(
                id=str(p.payload.block_id), vector=p.vector, payload=p.payload.to_payload()
            )
            for p in points
        ]
        await self._client.upsert(self.collection, points=structs, wait=True)

    async def search(
        self, vector: list[float], limit: int = 8, extra: models.Filter | None = None
    ) -> list[CanonicalHit]:
        response = await self._client.query_points(
            self.collection,
            query=vector,
            query_filter=restricted(extra),
            limit=limit,
            with_payload=True,
        )
        return [
            CanonicalHit(
                block_id=UUID(str(point.id)),
                article_id=UUID(str(payload["article_id"])),
                author_id=UUID(str(payload["author_id"])),
                is_honeypot=bool(payload.get("is_honeypot", False)),
                score=point.score,
            )
            for point in response.points
            if (payload := point.payload or {})
        ]

    async def set_payload(self, scope: CanonicalScope, payload: Mapping[str, Any]) -> None:
        await self._client.set_payload(
            self.collection,
            payload=dict(payload),
            points=models.FilterSelector(filter=scope.to_filter()),
            wait=True,
        )

    async def delete(self, scope: CanonicalScope) -> None:
        await self._client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=scope.to_filter()),
            wait=True,
        )

    async def delete_article_except(self, article_id: UUID, keep: Iterable[UUID]) -> None:
        """Delete the article's points whose block is not in ``keep`` (removed or revoked)."""
        keep_ids = list(keep)
        must_not: list[models.Condition] = [_has_id(keep_ids)] if keep_ids else []
        flt = models.Filter(must=[_match_any("article_id", [article_id])], must_not=must_not)
        await self._client.delete(
            self.collection, points_selector=models.FilterSelector(filter=flt), wait=True
        )

    async def count(self, scope: CanonicalScope) -> int:
        result = await self._client.count(
            self.collection, count_filter=scope.to_filter(), exact=True
        )
        return result.count

    async def retrieve(self, block_ids: Iterable[UUID]) -> list[StoredPoint]:
        records = await self._client.retrieve(
            self.collection, ids=[str(b) for b in block_ids], with_payload=True
        )
        return [StoredPoint(UUID(str(r.id)), dict(r.payload or {})) for r in records]

    async def active_vectors(self, batch: int = SCROLL_BATCH) -> AsyncIterator[list[list[float]]]:
        """Vectors of retrievable, non-honeypot points, in batches (topic clusters, 5.3)."""
        flt = models.Filter(must=[*_active_conditions(), _match("is_honeypot", False)])
        offset: Any = None
        while True:
            records, offset = await self._client.scroll(
                self.collection,
                scroll_filter=flt,
                limit=batch,
                offset=offset,
                with_payload=False,
                with_vectors=True,
            )
            if records:
                yield [_dense(r.vector) for r in records]
            if offset is None:
                return

    async def scroll(self, batch: int = SCROLL_BATCH) -> AsyncIterator[list[StoredPoint]]:
        """Every point, in batches (consistency audit, 4.4)."""
        offset: Any = None
        while True:
            records, offset = await self._client.scroll(
                self.collection, limit=batch, offset=offset, with_payload=True
            )
            if records:
                yield [StoredPoint(UUID(str(r.id)), dict(r.payload or {})) for r in records]
            if offset is None:
                return


# --- Writes to every live generation (10.3) --------------------------------------------------


class CanonicalFanout:
    """Consent and governance writes to the canonical collection of every live generation,
    read fresh on each write: a generation being built or just replaced gets them too."""

    def __init__(self, client: AsyncQdrantClient, generations: GenerationSource) -> None:
        self._client = client
        self._generations = generations

    async def repos(self) -> list[CanonicalVectorRepo]:
        return [
            CanonicalVectorRepo(self._client, g.canonical_collection)
            for g in await self._generations.live()
        ]

    async def set_payload(self, scope: CanonicalScope, payload: Mapping[str, Any]) -> None:
        for repo in await self.repos():
            await repo.set_payload(scope, payload)

    async def delete(self, scope: CanonicalScope) -> None:
        for repo in await self.repos():
            await repo.delete(scope)


class PrivateFanout:
    """Deletions from one member's partition of every live generation's private collection."""

    def __init__(
        self, client: AsyncQdrantClient, generations: GenerationSource, user_id: UUID
    ) -> None:
        self._client = client
        self._generations = generations
        self._user_id = user_id

    async def repos(self) -> list[PrivateVectorRepo]:
        return [
            PrivateVectorRepo(self._client, self._user_id, g.private_collection)
            for g in await self._generations.live()
        ]

    async def delete_derivative(self, derivative_id: UUID) -> None:
        for repo in await self.repos():
            await repo.delete_derivative(derivative_id)

    async def delete_note(self, note_id: UUID) -> None:
        for repo in await self.repos():
            await repo.delete_note(note_id)
