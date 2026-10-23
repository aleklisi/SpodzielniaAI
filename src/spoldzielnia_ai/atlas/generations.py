"""Generations of the visual atlas: which image model built which collection (10.6, P8).

The atlas follows the text index (``vector.generations``, 10.3): ``index_generation`` rows with
``family = IMAGE`` record the image embedding model and the dimension it produced; each
generation has one collection, ``atlas_visual_index__g<n>`` (``canonical_collection``). Every
search embeds the query crops with the serving generation's model; indexing writes to every
writable generation with its own model; consent and governance operations go to every live one.

The image model is, per configuration (no fallback after a failure, P5):

1. the model the ACTIVE configuration routes IMAGE_EMBED to (an embedding API, cleared for
   private data: atlas photos and members' recognition crops are private until approved);
2. otherwise the built-in SigLIP of worker-core, if the operator allows it
   (``llm.builtin_image_embed``) and its model is in the volume;
3. otherwise none: atlas indexing waits (``model_wait``) and „Rozpoznaj” is off.

Switching between them builds a new generation next to the serving one and swaps when every
approved photo is in it (``AtlasReEmbedder``). The collection built before 10.6 by the built-in
SigLIP is adopted as the first generation.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import func, or_, select, update

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    ConsentStatus,
    IndexGenerationStatus,
    LlmRevisionStatus,
    LlmRoutePurpose,
)
from spoldzielnia_ai.db.models import AtlasImage, IndexGeneration, LlmConfigRevision
from spoldzielnia_ai.db.models.index_generation import IMAGE
from spoldzielnia_ai.llm.gateway import LiteLLMGateway, LLMError, LLMPurpose, ModelNotConfigured
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot
from spoldzielnia_ai.vector.collections import (
    ATLAS_COLLECTION,
    collection_size,
    ensure_collection,
    generation_collection,
)
from spoldzielnia_ai.vector.generations import embed_key, resolve_embed
from spoldzielnia_ai.vector.reembed import (
    _RECONCILE_LOCK,  # pyright: ignore[reportPrivateUsage]
    BuildRun,
    GenerationError,
    drop_expired,
    swap_generation,
)
from spoldzielnia_ai.workers.models import IMAGE_EMBED_REVISION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

    from PIL.Image import Image
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.atlas.embedding import ImageEmbedder
    from spoldzielnia_ai.atlas.vectors import AtlasIndexer
    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import LlmSettings, QdrantSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import Deployment

log = get_logger(__name__)

Status = IndexGenerationStatus
WRITABLE = (Status.SERVING, Status.BUILDING)
BUILTIN_KEY = f"builtin/siglip-base-patch16-224@{IMAGE_EMBED_REVISION[:12]}"
BUILTIN_MODEL = "Wbudowany (SigLIP)"
API_BATCH = 16
API_MAX_SIDE = 768
BATCH = 50
SAVE_EVERY = 20
BUDGET_SECONDS = 240.0
_BUILD_LOCK = select(func.pg_try_advisory_xact_lock(func.hashtextextended("atlas_generation", 0)))

type BuiltinEmbedder = Callable[[], ImageEmbedder | None]


def _data_url(image: Image) -> str:
    copy = image.convert("RGB")
    copy.thumbnail((API_MAX_SIDE, API_MAX_SIDE))
    buffer = io.BytesIO()
    copy.save(buffer, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


class ApiImageEmbedder:
    """``ImageEmbedder`` over the gateway's ``embed_images`` with a pinned deployment. Blocking
    (like SigLIP): runs in a worker thread, each call on its own event loop."""

    def __init__(self, deployment: Deployment, settings: LlmSettings, dimension: int) -> None:
        self._deployment = deployment
        self._settings = settings
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, images: Sequence[Image]) -> list[list[float]]:
        return asyncio.run(self.aembed(images))

    async def aembed(self, images: Sequence[Image]) -> list[list[float]]:
        gateway = LiteLLMGateway(self._settings)  # the pinned deployment needs no routes
        vectors: list[list[float]] = []
        for start in range(0, len(images), API_BATCH):
            urls = [_data_url(image) for image in images[start : start + API_BATCH]]
            vectors += await gateway.embed_images(urls, deployment=self._deployment)
        return vectors


@dataclass(frozen=True, slots=True)
class ImageGeneration:
    number: int
    status: IndexGenerationStatus
    model: str
    dimension: int
    collection: str
    embedder: ImageEmbedder | None = None  # None: its model can't be used now


class ImageGenerationSource(Protocol):
    async def serving(self) -> ImageGeneration | None: ...

    async def writable(self) -> list[ImageGeneration]: ...

    async def live(self) -> list[ImageGeneration]: ...


@dataclass(frozen=True, slots=True)
class StaticImageGenerations:
    """Fixed generations (tests, tools): the first SERVING one serves."""

    generations: tuple[ImageGeneration, ...]

    async def serving(self) -> ImageGeneration | None:
        return next((g for g in self.generations if g.status == Status.SERVING), None)

    async def writable(self) -> list[ImageGeneration]:
        return [g for g in self.generations if g.status in WRITABLE]

    async def live(self) -> list[ImageGeneration]:
        return [g for g in self.generations if g.status != Status.CANCELLED]


@dataclass(frozen=True, slots=True)
class ImageSpec:
    """The image model the configuration selects (see the module docstring)."""

    key: str
    model: str
    deployment: dict[str, Any] | None  # SnapshotDeployment as JSON; None: .env or built-in
    revision: int

    @property
    def builtin(self) -> bool:
        return self.key == BUILTIN_KEY


async def active_image_embed(
    session: AsyncSession, settings: LlmSettings, builtin_available: bool
) -> ImageSpec | None:
    revision = 0
    if settings.config_source == "db":
        row = (
            await session.execute(
                select(LlmConfigRevision.number, LlmConfigRevision.snapshot).where(
                    LlmConfigRevision.status == LlmRevisionStatus.ACTIVE
                )
            )
        ).first()
        if row is not None and row.snapshot is not None:
            revision = row.number
            item = ConfigSnapshot.model_validate(row.snapshot).deployment_for(
                LlmRoutePurpose.IMAGE_EMBED
            )
            if item is not None:
                return ImageSpec(
                    embed_key(item.base_url, item.litellm_provider, item.model),
                    item.display_name,
                    item.model_dump(mode="json"),
                    revision,
                )
    elif settings.image_embed_model:
        return ImageSpec(
            embed_key(settings.local_base_url, "openai", settings.image_embed_model),
            settings.image_embed_model,
            None,
            0,
        )
    if builtin_available and settings.builtin_image_embed:
        return ImageSpec(BUILTIN_KEY, BUILTIN_MODEL, None, revision)
    return None


class ImageGenerationStore:
    """``ImageGenerationSource`` over ``index_generation`` (family IMAGE). Like the text store:
    ``serving()`` cached for ``ttl_seconds``, writers read fresh."""

    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        settings: LlmSettings,
        builtin: BuiltinEmbedder,
        ttl_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._settings = settings
        self._builtin = builtin
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[float, ImageGeneration | None] | None = None

    def forget(self) -> None:
        self._cached = None

    async def serving(self) -> ImageGeneration | None:
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        found = await self._load(IndexGeneration.status == Status.SERVING, resolve_model=True)
        serving = found[0] if found else None
        self._cached = (now, serving)
        return serving

    async def writable(self) -> list[ImageGeneration]:
        return await self._load(IndexGeneration.status.in_(WRITABLE), resolve_model=True)

    async def live(self) -> list[ImageGeneration]:
        return await self._load(
            or_(
                IndexGeneration.status.in_(WRITABLE),
                (IndexGeneration.status == Status.RETIRED) & IndexGeneration.dropped_at.is_(None),
            ),
            resolve_model=False,
        )

    async def _load(self, where: Any, *, resolve_model: bool) -> list[ImageGeneration]:
        async with self._factory() as session:
            rows = list(
                await session.scalars(
                    select(IndexGeneration)
                    .where(where, IndexGeneration.family == IMAGE)
                    .order_by(IndexGeneration.number)
                )
            )
            return [
                await self.generation(session, row, resolve_model=resolve_model) for row in rows
            ]

    async def embedder(
        self,
        session: AsyncSession,
        key: str,
        deployment: dict[str, Any] | None,
        dimension: int | None,
    ) -> ImageEmbedder | None:
        """The usable embedder of a model, or ``None``. API models are re-resolved live (the
        provider row, its endpoint and clearance), so un-clearing stops them at once."""
        if key == BUILTIN_KEY:
            return self._builtin() if self._settings.builtin_image_embed else None
        resolved = await resolve_embed(
            session, key, deployment, self._cipher, self._settings, LLMPurpose.IMAGE_EMBED
        )
        if resolved is None:
            return None
        return ApiImageEmbedder(resolved, self._settings, dimension or 0)

    async def generation(
        self, session: AsyncSession, row: IndexGeneration, *, resolve_model: bool = True
    ) -> ImageGeneration:
        embedder = None
        if resolve_model:
            embedder = await self.embedder(session, row.embed_key, row.deployment, row.dimension)
            if embedder is None:
                log.warning("atlas.generation_model_unusable", generation=row.number)
        return ImageGeneration(
            row.number, row.status, row.model, row.dimension, row.canonical_collection, embedder
        )


def _probe_image() -> Image:
    from PIL import Image as PilImage
    from PIL import ImageDraw

    image = PilImage.new("RGB", (224, 224), "white")
    ImageDraw.Draw(image).ellipse((40, 40, 184, 184), fill="orange")
    return image


async def count_photos(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AtlasImage)
            .where(
                AtlasImage.status == AtlasImageStatus.APPROVED,
                AtlasImage.consent_status == ConsentStatus.ACTIVE,
            )
        )
        or 0
    )


async def _one(session: AsyncSession, status: IndexGenerationStatus) -> IndexGeneration | None:
    return await session.scalar(
        select(IndexGeneration).where(
            IndexGeneration.status == status, IndexGeneration.family == IMAGE
        )
    )


class ImageGenerationManager:
    """Reconcile, start, cancel and swap atlas generations (see the module docstring)."""

    def __init__(
        self,
        factory: SessionFactory,
        client: AsyncQdrantClient,
        store: ImageGenerationStore,
        llm: LlmSettings,
        qdrant: QdrantSettings,
        builtin: BuiltinEmbedder,
    ) -> None:
        self._factory = factory
        self._client = client
        self._store = store
        self._llm = llm
        self._qdrant = qdrant
        self._builtin = builtin

    async def _spec(self, session: AsyncSession) -> ImageSpec | None:
        return await active_image_embed(session, self._llm, self._builtin() is not None)

    async def reconcile(self) -> bool:
        """True if a generation is being built (the build task should run)."""
        async with self._factory() as session, session.begin():
            await session.execute(_RECONCILE_LOCK)
            spec = await self._spec(session)
            serving = await _one(session, Status.SERVING)
            building = await _one(session, Status.BUILDING)
            if spec is not None:
                if building is not None and building.embed_key != spec.key:
                    await self._cancel(session, building, None, "model_changed")
                    building = None
                if building is None and (serving is None or serving.embed_key != spec.key):
                    if serving is None and spec.builtin and await _has_legacy(self._client):
                        await self._adopt(session, spec)
                    elif not await _cancelled_for(session, spec):
                        await self._create(session, spec, None)
                    building = await _one(session, Status.BUILDING)
        await drop_expired(self._factory, self._client)
        return building is not None

    async def start(self, actor_id: UUID | None) -> int:
        async with self._factory() as session, session.begin():
            await session.execute(_RECONCILE_LOCK)
            spec = await self._spec(session)
            if spec is None:
                raise GenerationError("model_not_configured")
            building = await _one(session, Status.BUILDING)
            if building is not None:
                await self._cancel(session, building, actor_id, "restarted")
            number = await self._create(session, spec, actor_id)
        if number is None:
            raise GenerationError("image_model_unavailable")
        return number

    async def swap(self, number: int) -> bool:
        return await swap_generation(self._factory, self._client, self._qdrant, number, IMAGE)

    async def _cancel(
        self, session: AsyncSession, row: IndexGeneration, actor_id: UUID | None, reason: str
    ) -> None:
        row.status = Status.CANCELLED
        row.retired_at = datetime.now(UTC)
        await AuditService(session).record(
            EventType.INDEX_GENERATION_CANCELLED,
            actor_id,
            AuditTarget("index_generation", str(row.number)),
            {"model": row.model, "reason": reason, "family": IMAGE},
        )

    async def _usable(self, session: AsyncSession, spec: ImageSpec) -> int | None:
        """The model's dimension from a probe on a synthetic image, or None (unusable/down)."""
        embedder = await self._store.embedder(session, spec.key, spec.deployment, None)
        if embedder is None:
            log.warning("atlas.generation_model_unusable", model=spec.model)
            return None
        try:
            (vector,) = await asyncio.to_thread(embedder.embed, [_probe_image()])
        except (LLMError, ValueError, OSError) as exc:
            log.warning("atlas.generation_probe_failed", model=spec.model, error=type(exc).__name__)
            return None
        return len(vector) or None

    async def _adopt(self, session: AsyncSession, spec: ImageSpec) -> int | None:
        """The 9.8 collection (built by the built-in SigLIP) becomes the first generation."""
        dimension = await self._usable(session, spec)
        if dimension is None:
            return None
        if await collection_size(self._client, ATLAS_COLLECTION) != dimension:
            return await self._create(session, spec, None)
        row = IndexGeneration(
            family=IMAGE,
            status=Status.SERVING,
            embed_key=spec.key,
            model=spec.model,
            deployment=spec.deployment,
            revision=spec.revision,
            dimension=dimension,
            private_collection="",
            canonical_collection=ATLAS_COLLECTION,
            cursor={"phase": "done"},
            serving_since=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        await ensure_collection(self._client, ATLAS_COLLECTION, dimension)
        log.info("atlas.generation_adopted", generation=row.number)
        return row.number

    async def _create(
        self, session: AsyncSession, spec: ImageSpec, actor_id: UUID | None
    ) -> int | None:
        dimension = await self._usable(session, spec)
        if dimension is None:
            return None
        row = IndexGeneration(
            family=IMAGE,
            status=Status.BUILDING,
            embed_key=spec.key,
            model=spec.model,
            deployment=spec.deployment,
            revision=spec.revision,
            dimension=dimension,
            private_collection="",
            canonical_collection="",
            cursor={},
            total=await count_photos(session),
        )
        session.add(row)
        await session.flush()
        row.canonical_collection = generation_collection(ATLAS_COLLECTION, row.number)
        await ensure_collection(self._client, row.canonical_collection, dimension)
        await AuditService(session).record(
            EventType.INDEX_GENERATION_STARTED,
            actor_id,
            AuditTarget("index_generation", str(row.number)),
            {"model": spec.model, "dimension": dimension, "items": row.total, "family": IMAGE},
        )
        log.info("atlas.generation_started", generation=row.number, model=spec.model)
        return row.number


async def _has_legacy(client: AsyncQdrantClient) -> bool:
    real = {c.name for c in (await client.get_collections()).collections}
    return ATLAS_COLLECTION in real


async def _cancelled_for(session: AsyncSession, spec: ImageSpec) -> bool:
    found = await session.scalar(
        select(IndexGeneration.id).where(
            IndexGeneration.family == IMAGE,
            IndexGeneration.status == Status.CANCELLED,
            IndexGeneration.embed_key == spec.key,
            IndexGeneration.revision == spec.revision,
        )
    )
    return found is not None


class AtlasReEmbedder:
    """Fills the BUILDING atlas generation: every APPROVED + ACTIVE photo, in id order, through
    ``AtlasIndexer`` restricted to that generation; resumable from ``cursor``; swaps at the end.
    Cloud models are throttled to ``llm.reembed_per_minute`` photos."""

    def __init__(
        self,
        factory: SessionFactory,
        store: ImageGenerationStore,
        indexer: AtlasIndexer,
        manager: ImageGenerationManager,
        llm: LlmSettings,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._factory = factory
        self._store = store
        self._indexer = indexer
        self._manager = manager
        self._llm = llm
        self._clock = clock
        self._sleep = sleep

    async def run(self, budget_seconds: float = BUDGET_SECONDS) -> BuildRun:
        async with self._factory() as lock, lock.begin():
            if not await lock.scalar(_BUILD_LOCK):
                return BuildRun(busy=True)
            return await self._run(budget_seconds)

    async def _run(self, budget_seconds: float) -> BuildRun:
        async with self._factory() as session:
            row = await _one(session, Status.BUILDING)
            if row is None:
                return BuildRun()
            generation = await self._store.generation(session, row)
            after: str | None = (row.cursor or {}).get("after")
            done, failed = row.done, row.failed
            local = row.embed_key == BUILTIN_KEY or row.deployment is None
            if row.deployment is not None:
                local = local or bool(row.deployment.get("kind") == "LOCAL")
        if generation.embedder is None:
            return BuildRun(generation.number, waiting=True)
        throttle = 0.0 if local else 60.0 / self._llm.reembed_per_minute
        deadline = self._clock() + budget_seconds
        processed = 0
        while True:
            ids = await self._next(after)
            if not ids:
                if not await self._save(generation.number, {"phase": "end"}, done, failed):
                    return BuildRun(generation.number, processed)
                swapped = await self._manager.swap(generation.number)
                self._store.forget()
                return BuildRun(generation.number, processed, swapped=swapped)
            for image_id in ids:
                try:
                    await self._indexer.index_image(image_id, [generation])
                except ModelNotConfigured:
                    await self._save(generation.number, _cursor(after), done, failed)
                    return BuildRun(generation.number, processed, waiting=True)
                except LLMError as exc:  # the model is down: resume on the next reconcile
                    log.warning("atlas.reembed_model_down", error=type(exc).__name__)
                    await self._save(generation.number, _cursor(after), done, failed)
                    return BuildRun(generation.number, processed, waiting=True)
                except Exception as exc:  # one unreadable photo must not stop the build
                    log.error("atlas.reembed_failed", image=str(image_id), error=type(exc).__name__)
                    failed += 1
                done += 1
                processed += 1
                after = str(image_id)
                if processed % SAVE_EVERY == 0 and not await self._save(
                    generation.number, _cursor(after), done, failed
                ):
                    return BuildRun(generation.number, processed)
                if throttle:
                    await self._sleep(throttle)
                if self._clock() >= deadline:
                    if not await self._save(generation.number, _cursor(after), done, failed):
                        return BuildRun(generation.number, processed)
                    return BuildRun(generation.number, processed, more=True)

    async def _next(self, after: str | None) -> list[UUID]:
        from uuid import UUID

        async with self._factory() as session:
            query = select(AtlasImage.id).where(
                AtlasImage.status == AtlasImageStatus.APPROVED,
                AtlasImage.consent_status == ConsentStatus.ACTIVE,
            )
            if after is not None:
                query = query.where(AtlasImage.id > UUID(after))
            return list(await session.scalars(query.order_by(AtlasImage.id).limit(BATCH)))

    async def _save(self, number: int, cursor: dict[str, Any], done: int, failed: int) -> bool:
        async with self._factory() as session, session.begin():
            updated = await session.scalar(
                update(IndexGeneration)
                .where(
                    IndexGeneration.number == number,
                    IndexGeneration.status == Status.BUILDING,
                )
                .values(cursor=cursor, done=done, failed=failed, updated_at=func.now())
                .returning(IndexGeneration.id)
            )
        return updated is not None


def _cursor(after: str | None) -> dict[str, Any]:
    return {"after": after} if after is not None else {}


async def image_generations_overview(factory: SessionFactory) -> list[IndexGeneration]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(IndexGeneration)
                .where(IndexGeneration.family == IMAGE)
                .order_by(IndexGeneration.number.desc())
                .limit(20)
            )
        )
