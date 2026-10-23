"""Chunk 10.6: the visual atlas on index generations: built-in SigLIP or an image embedding API,
switched without mixing vector spaces (P8), consent reaching every live generation.

Real Postgres and Qdrant. The "built-in" model is the fake colour embedder; the "API" model is an
OpenAI-style ``/embeddings`` server (respx) returning the colour embedding reversed, a different
vector space of the same dimension.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.atlas.generations import (
    AtlasReEmbedder,
    ImageGenerationManager,
    ImageGenerationStore,
)
from spoldzielnia_ai.atlas.vectors import AtlasIndexer, AtlasVectorRepo
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import IndexGenerationStatus
from spoldzielnia_ai.db.models import IndexGeneration
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.gateway import LiteLLMGateway, LLMPurpose, ModelNotConfigured
from spoldzielnia_ai.vector.collections import ATLAS_COLLECTION, recreate_atlas_collection
from spoldzielnia_ai.workers.jobs import AtlasIndexJob, TaskName
from spoldzielnia_ai.workers.waiting import ModelWaitResumer, park
from tests.fixtures.app import harness
from tests.fixtures.atlas import (
    DIM,
    CleanScanner,
    ColourEmbedder,
    RecordingQueue,
    World,
    approve_all,
    make_world,
    sent_photo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.atlas.embedding import ImageEmbedder
    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness
    from tests.integration.conftest import QdrantInstance

pytestmark = pytest.mark.integration

Status = IndexGenerationStatus
API = LlmSettings(config_source="env", image_embed_model="clip-vit")  # .env: an image API model
NO_API = LlmSettings(config_source="env")


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> AsyncIterator[Harness]:
    for collection in (await qdrant.get_collections()).collections:
        if collection.name.startswith(f"{ATLAS_COLLECTION}__g"):
            await qdrant.delete_collection(collection.name)
    await recreate_atlas_collection(qdrant, DIM)  # built by the "built-in" model before 10.6
    async with harness(settings, factory) as h:
        h.router.route(url__startswith=qdrant_server.url).pass_through()
        h.app.state.scanner = CleanScanner()
        h.app.state.processing_queue = RecordingQueue()
        h.app.state.qdrant = qdrant
        yield h


@pytest.fixture
async def world(h: Harness, factory: SessionFactory) -> World:
    return await make_world(h, factory, h.app.state.processing_queue)


class Api:
    """The image embedding server: the colour embedding, reversed (another vector space)."""

    def __init__(self, h: Harness) -> None:
        self.images = 0
        self.route = h.router.post("http://llm:11434/v1/embeddings").mock(side_effect=self.reply)

    def reply(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["modality"] == "image"
        images: list[Image.Image] = []
        for uri in body["input"]:
            assert uri.startswith("data:image/jpeg;base64,")  # inline, never a link
            images.append(Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))))
        self.images += len(images)
        vectors = [v[::-1] for v in ColourEmbedder().embed(images)]
        data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
        return httpx.Response(200, json={"data": data})


class Atlas:
    def __init__(
        self,
        world: World,
        qdrant: AsyncQdrantClient,
        llm: LlmSettings,
        builtin: ImageEmbedder | None,
    ) -> None:
        state = world.h.app.state
        self.world = world
        self.store = ImageGenerationStore(
            world.factory, state.cipher, llm, lambda: builtin, ttl_seconds=0
        )
        self.manager = ImageGenerationManager(
            world.factory, qdrant, self.store, llm, state.settings.qdrant, lambda: builtin
        )
        self.indexer = AtlasIndexer(
            world.factory, state.cipher, world.h.storage, self.store, qdrant
        )
        self.reembed = AtlasReEmbedder(world.factory, self.store, self.indexer, self.manager, llm)
        state.image_generations = self.store  # the api's consent writes go to every live one


async def _photo(world: World, color: str) -> UUID:
    alice = await world.h.login(roles=["MEMBER"], auth_time=int(time.time()))
    image_id = await sent_photo(world, alice, color)
    await approve_all(world.h, image_id)
    return UUID(image_id)


async def _rows(factory: SessionFactory) -> list[IndexGeneration]:
    async with factory() as session:
        return list(await session.scalars(select(IndexGeneration).order_by(IndexGeneration.number)))


def _query(color: str) -> list[float]:
    return ColourEmbedder().embed([Image.new("RGB", (8, 8), color)])[0]


async def test_the_pre_10_6_collection_is_adopted_for_the_builtin_model(
    world: World, qdrant: AsyncQdrantClient
) -> None:
    atlas = Atlas(world, qdrant, NO_API, ColourEmbedder())
    assert await atlas.manager.reconcile() is False  # adopted, nothing to build
    (row,) = await _rows(world.factory)
    assert (row.family, row.status, row.canonical_collection, row.dimension) == (
        "IMAGE",
        Status.SERVING,
        ATLAS_COLLECTION,
        DIM,
    )
    serving = await atlas.store.serving()
    assert serving is not None
    assert isinstance(serving.embedder, ColourEmbedder)


async def test_switching_to_an_api_model_rebuilds_the_atlas_and_never_mixes_spaces(
    world: World, qdrant: AsyncQdrantClient
) -> None:
    builtin = Atlas(world, qdrant, NO_API, ColourEmbedder())
    await builtin.manager.reconcile()
    orange = await _photo(world, "orange")
    await builtin.indexer.index_image(orange)
    api = Api(world.h)

    atlas = Atlas(world, qdrant, API, ColourEmbedder())  # the configuration now names an API
    assert await atlas.manager.reconcile() is True  # a new generation is being built
    old, new = await _rows(world.factory)
    assert (old.status, new.status, new.dimension) == (Status.SERVING, Status.BUILDING, DIM)
    serving = await atlas.store.serving()
    assert serving is not None
    assert (serving.number, isinstance(serving.embedder, ColourEmbedder)) == (old.number, True)

    run = await atlas.reembed.run()
    assert (run.processed, run.swapped) == (1, True)
    serving = await atlas.store.serving()
    assert serving is not None
    assert serving.number == new.number
    assert serving.collection == f"{ATLAS_COLLECTION}__g{new.number}"
    assert serving.embedder is not None
    assert not isinstance(serving.embedder, ColourEmbedder)

    # P8: a query embedded by the serving generation's model finds the photo in its own
    # collection; the built-in model's vector would be from another space there.
    picture = Image.new("RGB", (8, 8), "orange")
    query = (await asyncio.to_thread(serving.embedder.embed, [picture]))[0]
    hits = await AtlasVectorRepo(qdrant, serving.collection).search(query, limit=3)
    assert hits[0].image_id == orange
    assert hits[0].score > 0.95
    assert query == pytest.approx(_query("orange")[::-1], abs=0.02)  # the API's space
    assert api.images >= 3  # probe + the photo and its two marks + the query
    retired = (await _rows(world.factory))[0]
    assert retired.status == Status.RETIRED


async def test_consent_during_a_build_reaches_both_generations(
    world: World, qdrant: AsyncQdrantClient
) -> None:
    builtin = Atlas(world, qdrant, NO_API, ColourEmbedder())
    await builtin.manager.reconcile()
    Api(world.h)
    atlas = Atlas(world, qdrant, API, ColourEmbedder())
    await atlas.manager.reconcile()
    image_id = await _photo(world, "orange")
    assert await atlas.indexer.index_image(image_id) == 3  # both writable generations

    author_sub = await _author(world, image_id)
    author = await world.h.login(subject=author_sub, roles=["MEMBER"], auth_time=int(time.time()))
    response = await author.client.post(
        f"/me/contributions/atlas/{image_id}/suspend", headers=author.headers
    )
    assert response.status_code == 200, response.text
    for generation in await atlas.store.live():
        payloads = await AtlasVectorRepo(qdrant, generation.collection).payloads(image_id)
        assert payloads, generation.collection
        assert {(p["consent_status"], p["is_active"]) for p in payloads} == {("SUSPENDED", False)}


async def test_without_an_image_model_indexing_waits(
    world: World, qdrant: AsyncQdrantClient
) -> None:
    await qdrant.delete_collection(ATLAS_COLLECTION)
    atlas = Atlas(world, qdrant, NO_API, None)  # no API, no built-in model
    assert await atlas.manager.reconcile() is False
    assert await _rows(world.factory) == []
    image_id = await _photo(world, "orange")
    with pytest.raises(ModelNotConfigured):
        await atlas.indexer.index_image(image_id)
    # What worker-core does then: park the job (no route exists for the built-in model).
    await park(world.factory, image_id, None, "IMAGE_EMBED", TaskName.INDEX_ATLAS)
    sent: list[tuple[TaskName, Any]] = []

    async def record(name: TaskName, job: Any) -> None:
        sent.append((name, job))

    resumer = ModelWaitResumer(world.factory, LiteLLMGateway(NO_API), record)
    assert await resumer.run() == 0  # no route, and no built-in model yet

    later = Atlas(world, qdrant, NO_API, ColourEmbedder())  # the model is installed later
    assert await later.manager.reconcile() is True  # a first generation, filled by the build
    run = await later.reembed.run()
    assert run.swapped
    assert await resumer.run({LLMPurpose.IMAGE_EMBED}) == 1  # served without a route now
    assert sent == [(TaskName.INDEX_ATLAS, AtlasIndexJob(image_id=image_id))]
    serving = await later.store.serving()
    assert serving is not None
    assert await AtlasVectorRepo(qdrant, serving.collection).payloads(image_id)


async def _author(world: World, image_id: UUID) -> str:
    from spoldzielnia_ai.db.models import AtlasImage, User

    async with world.factory() as session:
        image = await session.get(AtlasImage, image_id)
        assert image is not None
        user = await session.get(User, image.author_id)
        assert user is not None
        return user.oidc_subject
