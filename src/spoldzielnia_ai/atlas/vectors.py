"""The visual atlas in Qdrant (chunk 9.8): ``atlas_visual_index``.

One point per approved photo (``kind: image``) and one per mark (``kind: region``, a crop with a
margin). Payloads hold ids and governance fields only (overview §2.5, A4): ``atlas_image_id``,
``region_id``, ``label_id``, ``domain_id``, ``author_id``, ``consent_status``, ``is_active``,
``kind``, ``verified_at``. Point ids are derived from (photo, region), so re-indexing
overwrites.

- ``AtlasVectorRepo``: every search ANDs the mandatory filter (``consent_status == ACTIVE`` and
  ``is_active``); callers may narrow it, never widen it (A5).
- ``AtlasOp``: operations recorded in the shared ``vector_outbox`` (``filter_json.target =
  "atlas"``), applied in commit order with the wiki's operations (4.4).
- ``AtlasIndexer``: reads the photo's current state and (re)builds its points; a photo that is
  no longer APPROVED + ACTIVE loses them.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from qdrant_client import models
from sqlalchemy import select

from spoldzielnia_ai.atlas.embedding import Box, crop
from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus, VectorOpKind
from spoldzielnia_ai.db.models import AtlasImage, AtlasRegion
from spoldzielnia_ai.vector.collections import ATLAS_COLLECTION

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.atlas.embedding import ImageEmbedder
    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.storage import ObjectStorage

log = get_logger(__name__)

TARGET = "atlas"
_POINT_NS = UUID("3b7f4d2e-9a61-4c0b-8e5f-1d2a3c4b5e6f")


def point_id(image_id: UUID, region_id: UUID | None) -> UUID:
    return uuid.uuid5(_POINT_NS, f"{image_id}:{region_id or 'image'}")


def _match(key: str, value: str | bool) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _match_any(key: str, values: Iterable[UUID]) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchAny(any=[str(v) for v in values]))


def active_atlas_conditions() -> list[models.Condition]:
    return [_match("consent_status", ConsentStatus.ACTIVE.value), _match("is_active", True)]


@dataclass(frozen=True, slots=True)
class AtlasScope:
    """Which atlas points a write applies to: never the whole collection."""

    image_ids: tuple[UUID, ...] | None = None
    label_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.image_ids and self.label_id is None:
            raise ValueError("an atlas scope needs image_ids or label_id")

    def to_filter(self) -> models.Filter:
        must: list[models.Condition] = []
        if self.image_ids:
            must.append(_match_any("atlas_image_id", self.image_ids))
        if self.label_id is not None:
            must.append(_match("label_id", str(self.label_id)))
        return models.Filter(must=must)

    def to_json(self) -> dict[str, Any]:
        return {
            "target": TARGET,
            "image_ids": [str(i) for i in self.image_ids] if self.image_ids else None,
            "label_id": str(self.label_id) if self.label_id else None,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> AtlasScope:
        ids = data.get("image_ids")
        label = data.get("label_id")
        return cls(
            image_ids=tuple(UUID(str(i)) for i in ids) if ids else None,
            label_id=UUID(str(label)) if label else None,
        )


@dataclass(frozen=True, slots=True)
class AtlasOp:
    kind: VectorOpKind
    scope: AtlasScope
    payload: dict[str, Any] = field(default_factory=dict[str, Any])

    @property
    def filter_json(self) -> dict[str, Any]:
        return self.scope.to_json()


def index_images(image_ids: Iterable[UUID]) -> AtlasOp:
    return AtlasOp(VectorOpKind.UPSERT, AtlasScope(image_ids=tuple(image_ids)))


def suspend_image(image_id: UUID) -> AtlasOp:
    return AtlasOp(
        VectorOpKind.SET_PAYLOAD,
        AtlasScope(image_ids=(image_id,)),
        {"consent_status": ConsentStatus.SUSPENDED.value, "is_active": False},
    )


def resume_image(image_id: UUID, *, approved: bool) -> AtlasOp:
    payload: dict[str, Any] = {"consent_status": ConsentStatus.ACTIVE.value}
    if approved:
        payload["is_active"] = True
    return AtlasOp(VectorOpKind.SET_PAYLOAD, AtlasScope(image_ids=(image_id,)), payload)


def delete_image(image_id: UUID) -> AtlasOp:
    return AtlasOp(VectorOpKind.DELETE, AtlasScope(image_ids=(image_id,)))


def relabel(old: UUID, new: UUID) -> AtlasOp:
    """A label merge re-pointed the marks in Postgres; their points follow."""
    return AtlasOp(VectorOpKind.SET_PAYLOAD, AtlasScope(label_id=old), {"label_id": str(new)})


@dataclass(frozen=True, slots=True)
class AtlasPoint:
    image_id: UUID
    region_id: UUID | None
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AtlasHit:
    image_id: UUID
    region_id: UUID | None
    label_id: UUID | None
    score: float


class AtlasVectorRepo:
    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def upsert(self, points: list[AtlasPoint]) -> None:
        if not points:
            return
        structs = [
            models.PointStruct(
                id=str(point_id(p.image_id, p.region_id)), vector=p.vector, payload=p.payload
            )
            for p in points
        ]
        await self._client.upsert(ATLAS_COLLECTION, points=structs, wait=True)

    async def search(
        self,
        vector: list[float],
        *,
        limit: int,
        domain_id: UUID | None = None,
        kind: str | None = None,
        min_score: float | None = None,
    ) -> list[AtlasHit]:
        """Only ACTIVE, active points (A5): the mandatory filter is always ANDed."""
        must = active_atlas_conditions()
        if domain_id is not None:
            must.append(_match("domain_id", str(domain_id)))
        if kind is not None:
            must.append(_match("kind", kind))
        response = await self._client.query_points(
            ATLAS_COLLECTION,
            query=vector,
            query_filter=models.Filter(must=must),
            limit=limit,
            with_payload=True,
            score_threshold=min_score,
        )
        hits: list[AtlasHit] = []
        for point in response.points:
            payload = point.payload or {}
            region = payload.get("region_id")
            label = payload.get("label_id")
            hits.append(
                AtlasHit(
                    image_id=UUID(str(payload["atlas_image_id"])),
                    region_id=UUID(str(region)) if region else None,
                    label_id=UUID(str(label)) if label else None,
                    score=point.score,
                )
            )
        return hits

    async def set_payload(self, scope: AtlasScope, payload: Mapping[str, Any]) -> None:
        await self._client.set_payload(
            ATLAS_COLLECTION,
            payload=dict(payload),
            points=models.FilterSelector(filter=scope.to_filter()),
            wait=True,
        )

    async def delete(self, scope: AtlasScope) -> None:
        await self._client.delete(
            ATLAS_COLLECTION,
            points_selector=models.FilterSelector(filter=scope.to_filter()),
            wait=True,
        )

    async def count(self, scope: AtlasScope) -> int:
        result = await self._client.count(
            ATLAS_COLLECTION, count_filter=scope.to_filter(), exact=True
        )
        return result.count

    async def payloads(self, image_id: UUID) -> list[dict[str, Any]]:
        records, _ = await self._client.scroll(
            ATLAS_COLLECTION,
            scroll_filter=AtlasScope(image_ids=(image_id,)).to_filter(),
            limit=1000,
            with_payload=True,
        )
        return [dict(r.payload or {}) for r in records]


def atlas_payload(
    image: AtlasImage, region: AtlasRegion | None, label_id: UUID | None
) -> dict[str, Any]:
    """Ids and governance fields only: no caption, no description, no label name."""
    active = (
        image.status == AtlasImageStatus.APPROVED and image.consent_status == ConsentStatus.ACTIVE
    )
    return {
        "atlas_image_id": str(image.id),
        "region_id": str(region.id) if region else None,
        "label_id": str(label_id) if label_id else None,
        "domain_id": str(image.domain_id),
        "author_id": str(image.author_id),
        "consent_status": image.consent_status.value,
        "is_active": active,
        "kind": "region" if region else "image",
        "verified_at": image.verified_at.isoformat() if image.verified_at else None,
    }


class AtlasIndexer:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        storage: ObjectStorage,
        embedder: ImageEmbedder,
        repo: AtlasVectorRepo,
    ) -> None:
        self._factory = factory
        self._objects = AtlasObjects(cipher, storage)
        self._embedder = embedder
        self._repo = repo

    async def index_image(self, image_id: UUID) -> int:
        """(Re)build the photo's points from its current state. Returns the point count."""
        async with self._factory() as session:
            image = await session.get(AtlasImage, image_id)
            regions: list[AtlasRegion] = []
            labels: dict[UUID, UUID] = {}
            if image is not None:
                regions = list(
                    await session.scalars(
                        select(AtlasRegion)
                        .where(AtlasRegion.atlas_image_id == image.id)
                        .order_by(AtlasRegion.position)
                    )
                )
                resolved = await LabelService(session).resolve({r.label_id for r in regions})
                labels = {k: v.id for k, v in resolved.items()}
        scope = AtlasScope(image_ids=(image_id,))
        if (
            image is None
            or image.status != AtlasImageStatus.APPROVED
            or image.consent_status != ConsentStatus.ACTIVE
        ):
            await self._repo.delete(scope)
            return 0
        data = await self._objects.read(image, "full")
        if data is None:
            await self._repo.delete(scope)
            return 0
        vectors = await asyncio.to_thread(self._embed, data, regions)
        points = [AtlasPoint(image.id, None, vectors[0], atlas_payload(image, None, None))]
        points += [
            AtlasPoint(
                image.id,
                region.id,
                vector,
                atlas_payload(image, region, labels.get(region.label_id, region.label_id)),
            )
            for region, vector in zip(regions, vectors[1:], strict=True)
        ]
        await self._repo.delete(scope)  # marks removed by a correction leave no point behind
        await self._repo.upsert(points)
        log.info("atlas.indexed", image=str(image_id), points=len(points))
        return len(points)

    def _embed(self, data: bytes, regions: list[AtlasRegion]) -> list[list[float]]:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as source:
            whole = source.convert("RGB")
        crops = [crop(whole, Box(r.x, r.y, r.w, r.h)) for r in regions]
        return self._embedder.embed([whole, *crops])
