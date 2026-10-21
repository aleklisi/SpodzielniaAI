"""Chunk 9.8: ``atlas_visual_index`` follows approval, consent and label merges through the
outbox, carries provenance only, and searches never return inactive points (A4, A5).

Real Postgres and Qdrant; a small fake image embedder (mean colour) instead of SigLIP.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.atlas.consistency import AtlasConsistency
from spoldzielnia_ai.atlas.vectors import AtlasIndexer, AtlasScope, AtlasVectorRepo
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.vector.collections import recreate_atlas_collection
from spoldzielnia_ai.vector.deps import get_vector_sync
from tests.fixtures.app import harness
from tests.fixtures.atlas import (
    MFA,
    CleanScanner,
    RecordingQueue,
    World,
    approve_all,
    make_world,
    sent_photo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness
    from tests.integration.conftest import QdrantInstance

pytestmark = pytest.mark.integration

DIM = 8


class ColourEmbedder:
    """Brightness of a 4x2 grid, normalised: enough to tell the test photos apart."""

    dimension = DIM

    def embed(self, images: Sequence[Image.Image]) -> list[list[float]]:
        out: list[list[float]] = []
        for image in images:
            small = image.convert("L").resize((4, 2)).tobytes()
            values = [b / 255 + 0.01 for b in small]
            norm = math.sqrt(sum(v * v for v in values))
            out.append([v / norm for v in values])
        return out


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
async def h(
    settings: Settings,
    factory: SessionFactory,
    queue: RecordingQueue,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> AsyncIterator[Harness]:
    await recreate_atlas_collection(qdrant, DIM)
    async with harness(settings, factory) as h:
        h.router.route(url__startswith=qdrant_server.url).pass_through()  # the real Qdrant
        h.app.state.scanner = CleanScanner()
        h.app.state.processing_queue = queue
        h.app.state.qdrant = qdrant
        yield h


@pytest.fixture
async def world(h: Harness, factory: SessionFactory, queue: RecordingQueue) -> World:
    return await make_world(h, factory, queue)


def indexer(world: World, qdrant: AsyncQdrantClient) -> AtlasIndexer:
    state = world.h.app.state
    return AtlasIndexer(
        world.factory, state.cipher, world.h.storage, ColourEmbedder(), AtlasVectorRepo(qdrant)
    )


async def payloads(qdrant: AsyncQdrantClient, image_id: str) -> list[dict[str, Any]]:
    return await AtlasVectorRepo(qdrant).payloads(UUID(image_id))


async def approved_and_indexed(
    world: World, qdrant: AsyncQdrantClient, color: str = "orange"
) -> str:
    alice = await world.h.login(roles=["MEMBER"], auth_time=int(time.time()))
    image_id = await sent_photo(world, alice, color)
    await approve_all(world.h, image_id)
    # The approval queued the photo through the outbox; worker-core indexes it.
    assert world.queue.indexed[-1] == UUID(image_id)
    assert await indexer(world, qdrant).index_image(UUID(image_id)) == 3
    return image_id


async def test_approval_indexes_photo_and_marks_with_provenance(
    world: World, qdrant: AsyncQdrantClient
) -> None:
    image_id = await approved_and_indexed(world, qdrant)
    points = await payloads(qdrant, image_id)
    assert sorted(p["kind"] for p in points) == ["image", "region", "region"]
    assert {p["consent_status"] for p in points} == {"ACTIVE"}
    assert all(p["is_active"] for p in points)
    assert {p["label_id"] for p in points if p["kind"] == "region"} == {world.queen, world.cell}
    text = str(points)
    assert "Matka" not in text
    assert "zielono" not in text
    # Re-indexing overwrites (deterministic ids), never duplicates.
    await indexer(world, qdrant).index_image(UUID(image_id))
    assert len(await payloads(qdrant, image_id)) == 3


async def test_photos_in_review_are_not_indexed(world: World, qdrant: AsyncQdrantClient) -> None:
    alice = await world.h.login(roles=["MEMBER"])
    image_id = await sent_photo(world, alice)
    assert world.queue.indexed == []
    assert await indexer(world, qdrant).index_image(UUID(image_id)) == 0
    assert await payloads(qdrant, image_id) == []


async def test_consent_reaches_the_index_at_once(world: World, qdrant: AsyncQdrantClient) -> None:
    image_id = await approved_and_indexed(world, qdrant)
    author = await world.h.login(
        subject=await _subject(world, image_id), roles=["MEMBER"], auth_time=int(time.time())
    )
    repo = AtlasVectorRepo(qdrant)
    query = ColourEmbedder().embed([Image.new("RGB", (8, 8), "orange")])[0]
    assert {h.image_id for h in await repo.search(query, limit=10)} == {UUID(image_id)}

    await author.client.post(f"/me/contributions/atlas/{image_id}/suspend", headers=author.headers)
    assert {(p["consent_status"], p["is_active"]) for p in await payloads(qdrant, image_id)} == {
        ("SUSPENDED", False)
    }
    assert await repo.search(query, limit=10) == []  # the mandatory filter

    await author.client.post(f"/me/contributions/atlas/{image_id}/resume", headers=author.headers)
    assert {(p["consent_status"], p["is_active"]) for p in await payloads(qdrant, image_id)} == {
        ("ACTIVE", True)
    }

    revoked = await author.client.post(
        f"/me/contributions/atlas/{image_id}/revoke",
        json={"confirm": "REVOKE"},
        headers=author.headers,
    )
    assert revoked.status_code == 200
    assert await payloads(qdrant, image_id) == []


async def test_label_merge_repoints_points(world: World, qdrant: AsyncQdrantClient) -> None:
    image_id = await approved_and_indexed(world, qdrant)
    admin = await world.h.login(roles=["MEMBER", "ADMIN"], **MFA)
    await admin.client.post(
        f"/atlas/curation/labels/{world.cell}/merge",
        json={"into_id": world.queen},
        headers=admin.headers,
    )
    regions = [p for p in await payloads(qdrant, image_id) if p["kind"] == "region"]
    assert {p["label_id"] for p in regions} == {world.queen}


async def test_consistency_audit_repairs_drift(world: World, qdrant: AsyncQdrantClient) -> None:
    image_id = await approved_and_indexed(world, qdrant)
    other = await approved_and_indexed(world, qdrant, "green")
    repo = AtlasVectorRepo(qdrant)
    await repo.set_payload(AtlasScope(image_ids=(UUID(image_id),)), {"is_active": False})  # drift
    await repo.delete(AtlasScope(image_ids=(UUID(other),)))  # lost points
    world.queue.indexed.clear()

    sync = get_vector_sync(_request(world.h))
    queued: list[UUID] = []

    async def requeue(image: UUID) -> None:
        queued.append(image)

    report = await AtlasConsistency(world.factory, qdrant, sync, requeue).run()
    assert (report.fixed, report.queued, report.deleted) == (1, 1, 0)
    assert queued == [UUID(other)]
    assert {p["is_active"] for p in await payloads(qdrant, image_id)} == {True}
    again = await AtlasConsistency(world.factory, qdrant, sync, requeue).run()
    assert (again.fixed, again.deleted) == (0, 0)


def _request(h: Harness) -> Any:
    class _Request:
        app = h.app

    return _Request()


async def _subject(world: World, image_id: str) -> str:
    from spoldzielnia_ai.db.models import AtlasImage, User

    async with world.factory() as session:
        image = await session.get(AtlasImage, UUID(image_id))
        assert image is not None
        user = await session.get(User, image.author_id)
        assert user is not None
        return user.oidc_subject
