"""Consistency of the atlas generations' collections with Postgres (chunk 9.8, 10.6).

Nightly, with the wiki's audit (4.4), under the index-writer lock, for every live atlas
generation:

- points of photos that no longer exist, or are REVOKED, or not APPROVED → deleted;
- points whose consent or ``is_active`` differ from the photo's state → payload written back;
- an APPROVED + ACTIVE photo without points in a writable generation → queued for indexing
  (which writes every writable generation).

Changing the image model builds a new generation (``atlas.generations``); nothing is dropped
up front.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from spoldzielnia_ai.atlas.vectors import AtlasScope, AtlasVectorRepo
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus
from spoldzielnia_ai.db.models import AtlasImage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.atlas.generations import ImageGenerationSource
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.vector.sync import VectorSync

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AtlasDrift:
    photos: int
    deleted: int
    fixed: int
    queued: int

    @property
    def drifted(self) -> bool:
        return bool(self.deleted or self.fixed or self.queued)


async def _point_states(
    client: AsyncQdrantClient, collection: str
) -> dict[UUID, set[tuple[str, bool]]]:
    """Per photo: the (consent_status, is_active) pairs its points carry."""
    states: dict[UUID, set[tuple[str, bool]]] = {}
    offset: Any = None
    while True:
        records, offset = await client.scroll(
            collection, limit=256, offset=offset, with_payload=True, with_vectors=False
        )
        for record in records:
            payload = record.payload or {}
            image_id = UUID(str(payload["atlas_image_id"]))
            states.setdefault(image_id, set()).add(
                (str(payload.get("consent_status")), bool(payload.get("is_active")))
            )
        if offset is None:
            return states


class AtlasConsistency:
    def __init__(
        self,
        factory: SessionFactory,
        client: AsyncQdrantClient,
        sync: VectorSync,
        reindex: Callable[[UUID], Awaitable[None]],
        generations: ImageGenerationSource,
    ) -> None:
        self._factory = factory
        self._client = client
        self._sync = sync
        self._reindex = reindex
        self._generations = generations

    async def run(self) -> AtlasDrift:
        total = AtlasDrift(0, 0, 0, 0)
        writable = {g.number for g in await self._generations.writable()}
        queued: set[UUID] = set()
        for generation in await self._generations.live():
            drift = await self._audit(generation.collection, generation.number in writable, queued)
            total = AtlasDrift(
                max(total.photos, drift.photos),
                total.deleted + drift.deleted,
                total.fixed + drift.fixed,
                len(queued),
            )
        return total

    async def _audit(self, collection: str, writable: bool, queued: set[UUID]) -> AtlasDrift:
        repo = AtlasVectorRepo(self._client, collection)
        deleted = fixed = 0
        queue: list[UUID] = []
        async with self._sync.exclusive() as session:
            points = await _point_states(self._client, collection)
            images = {
                i.id: i
                for i in await session.scalars(
                    select(AtlasImage).where(AtlasImage.id.in_(set(points)))
                )
            }
            for image_id, states in points.items():
                image = images.get(image_id)
                scope = AtlasScope(image_ids=(image_id,))
                if (
                    image is None
                    or image.consent_status == ConsentStatus.REVOKED
                    or image.status != AtlasImageStatus.APPROVED
                ):
                    await repo.delete(scope)
                    deleted += 1
                    continue
                expected = (
                    image.consent_status.value,
                    image.consent_status == ConsentStatus.ACTIVE,
                )
                if states != {expected}:
                    await repo.set_payload(
                        scope, {"consent_status": expected[0], "is_active": expected[1]}
                    )
                    fixed += 1
            live = await session.scalars(
                select(AtlasImage.id).where(
                    AtlasImage.status == AtlasImageStatus.APPROVED,
                    AtlasImage.consent_status == ConsentStatus.ACTIVE,
                )
            )
            queue = [i for i in live if i not in points and i not in queued] if writable else []
        for image_id in queue:
            queued.add(image_id)
            await self._reindex(image_id)
        report = AtlasDrift(len(points), deleted, fixed, len(queue))
        if report.drifted:
            async with self._factory() as session, session.begin():
                await AuditService(session).record(
                    EventType.VECTOR_DRIFT_DETECTED,
                    None,
                    AuditTarget("qdrant_collection", collection),
                    {
                        "photos": report.photos,
                        "deleted": deleted,
                        "fixed": fixed,
                        "queued": len(queue),
                    },
                )
            log.error("atlas_audit.drift", deleted=deleted, fixed=fixed, queued=len(queue))
        else:
            log.info("atlas_audit.clean", photos=report.photos)
        return report
