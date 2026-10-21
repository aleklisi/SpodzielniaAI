"""Consistency of ``atlas_visual_index`` with Postgres, and its rebuild (chunk 9.8).

Nightly, with the wiki's audit (4.4), under the index-writer lock:

- points of photos that no longer exist, or are REVOKED, or not APPROVED → deleted;
- points whose consent or ``is_active`` differ from the photo's state → payload written back;
- an APPROVED + ACTIVE photo without points → queued for indexing.

``rebuild_atlas`` drops the collection (e.g. after changing the image model) and queues every
APPROVED + ACTIVE photo.
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
from spoldzielnia_ai.vector.collections import ATLAS_COLLECTION, recreate_atlas_collection

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from qdrant_client import AsyncQdrantClient

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


async def _point_states(client: AsyncQdrantClient) -> dict[UUID, set[tuple[str, bool]]]:
    """Per photo: the (consent_status, is_active) pairs its points carry."""
    states: dict[UUID, set[tuple[str, bool]]] = {}
    offset: Any = None
    while True:
        records, offset = await client.scroll(
            ATLAS_COLLECTION, limit=256, offset=offset, with_payload=True, with_vectors=False
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
    ) -> None:
        self._factory = factory
        self._client = client
        self._repo = AtlasVectorRepo(client)
        self._sync = sync
        self._reindex = reindex

    async def run(self) -> AtlasDrift:
        deleted = fixed = 0
        queue: list[UUID] = []
        async with self._sync.exclusive() as session:
            points = await _point_states(self._client)
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
                    await self._repo.delete(scope)
                    deleted += 1
                    continue
                expected = (
                    image.consent_status.value,
                    image.consent_status == ConsentStatus.ACTIVE,
                )
                if states != {expected}:
                    await self._repo.set_payload(
                        scope, {"consent_status": expected[0], "is_active": expected[1]}
                    )
                    fixed += 1
            live = await session.scalars(
                select(AtlasImage.id).where(
                    AtlasImage.status == AtlasImageStatus.APPROVED,
                    AtlasImage.consent_status == ConsentStatus.ACTIVE,
                )
            )
            queue = [i for i in live if i not in points]
        for image_id in queue:
            await self._reindex(image_id)
        report = AtlasDrift(len(points), deleted, fixed, len(queue))
        if report.drifted:
            async with self._factory() as session, session.begin():
                await AuditService(session).record(
                    EventType.VECTOR_DRIFT_DETECTED,
                    None,
                    AuditTarget("qdrant_collection", ATLAS_COLLECTION),
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


async def rebuild_atlas(
    factory: SessionFactory,
    client: AsyncQdrantClient,
    image_vector_size: int,
    index: Callable[[UUID], Awaitable[int]],
) -> int:
    """Recreate the collection and index every APPROVED + ACTIVE photo. Returns the count."""
    await recreate_atlas_collection(client, image_vector_size)
    async with factory() as session:
        ids = list(
            await session.scalars(
                select(AtlasImage.id).where(
                    AtlasImage.status == AtlasImageStatus.APPROVED,
                    AtlasImage.consent_status == ConsentStatus.ACTIVE,
                )
            )
        )
    for image_id in ids:
        await index(image_id)
    log.info("reindex.atlas_done", photos=len(ids))
    return len(ids)
