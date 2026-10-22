"""Changing the embedding model safely: build a new index generation, then swap (10.3, P8).

``GenerationManager.reconcile`` (worker-core, every minute and right after an admin activates
a configuration, rolls back or clears a provider) compares the embedding model the ACTIVE
configuration routes EMBED to with the generations:

- none yet: the collections built before 10.3 are **adopted** as the first generation (they
  were built with the active model, which could not change before), or a first generation is
  created, empty, and filled by the build;
- the model differs from the serving one: a **BUILDING** generation is created for it (its
  dimension taken from a probe of the model, never from configuration) and the re-embed job
  starts. The serving generation keeps answering, with its own model, meanwhile;
- the configuration went back to the serving model: the BUILDING one is cancelled;
- RETIRED generations past their grace period, and CANCELLED ones, are dropped.

``ReEmbedder.run`` (task ``index.reembed``) fills the BUILDING generation: every CANONICAL
article, then every member's finished derivatives (read as their owner, through RLS), in id
order, through the same ``Indexer`` code as live indexing (locking included). The position is
saved in ``cursor``, so a run that stops (time budget, model down, worker restart) resumes where
it left off. Changes made meanwhile reach the new generation anyway: indexing writes to every
writable generation and the outbox to every live one. When the cursor is past the end, the
generations **swap** in one transaction; the old one is RETIRED and dropped after
``qdrant.retired_grace_hours``.

Cost guard: for a model that is not on a local endpoint, at most ``llm.reembed_per_minute``
items per minute; the admin sees how many items a switch re-embeds (``count_items``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, or_, select, update

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ArticleStatus, DerivativeStatus, IndexGenerationStatus
from spoldzielnia_ai.db.models import IndexGeneration, NoteDerivative, User, WikiArticle
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.llm.gateway import LLMError
from spoldzielnia_ai.vector.collections import (
    CANONICAL_COLLECTION,
    PRIVATE_COLLECTION,
    collection_size,
    drop_collections,
    ensure_collections,
    generation_collection,
    point_aliases,
)
from spoldzielnia_ai.vector.generations import (
    EmbedSpec,
    Generation,
    GenerationStore,
    active_embed,
    generation_of,
    resolve_embed,
)
from spoldzielnia_ai.vector.indexing import Outcome
from spoldzielnia_ai.workers.jobs import ArticleJob, DerivativeJob

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import LlmSettings, QdrantSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import Deployment, LLMGateway
    from spoldzielnia_ai.vector.indexing import Indexer

log = get_logger(__name__)

Status = IndexGenerationStatus
PROBE_TEXT = "Spółdzielnia: próbka do ustalenia wymiaru wektora."
BATCH = 50
SAVE_EVERY = 20
BUDGET_SECONDS = 240.0
_RECONCILE_LOCK = select(func.pg_advisory_xact_lock(func.hashtextextended("index_generation", 0)))
_BUILD_LOCK = select(
    func.pg_try_advisory_xact_lock(func.hashtextextended("index_generation_build", 0))
)
LEGACY = (PRIVATE_COLLECTION, CANONICAL_COLLECTION)


class GenerationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def count_items(factory: SessionFactory) -> int:
    """How many items a re-embed processes: CANONICAL articles and finished derivatives."""
    async with factory() as session:
        articles = await session.scalar(
            select(func.count())
            .select_from(WikiArticle)
            .where(WikiArticle.status == ArticleStatus.CANONICAL)
        )
        users = list(await session.scalars(select(User.id)))
    derivatives = 0
    for user_id in users:
        async with rls_session(factory, user_id) as session:
            derivatives += int(
                await session.scalar(
                    select(func.count())
                    .select_from(NoteDerivative)
                    .where(NoteDerivative.status == DerivativeStatus.DONE)
                )
                or 0
            )
    return int(articles or 0) + derivatives


@dataclass(frozen=True, slots=True)
class Reconciled:
    building: bool  # a generation is being built: the re-embed job should run
    created: int | None = None
    adopted: int | None = None
    cancelled: int | None = None
    dropped: int = 0


class GenerationManager:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        client: AsyncQdrantClient,
        gateway: LLMGateway,
        llm: LlmSettings,
        qdrant: QdrantSettings,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._client = client
        self._gateway = gateway
        self._llm = llm
        self._qdrant = qdrant

    async def reconcile(self) -> Reconciled:
        created = adopted = cancelled = None
        async with self._factory() as session, session.begin():
            await session.execute(_RECONCILE_LOCK)
            spec = await active_embed(session, self._llm)
            serving = await _one(session, Status.SERVING)
            building = await _one(session, Status.BUILDING)
            if spec is not None:
                if building is not None and building.embed_key != spec.key:
                    cancelled = await self._cancel(session, building, None, "model_changed")
                    building = None
                if building is None and (serving is None or serving.embed_key != spec.key):
                    if serving is None and await _has_legacy(self._client):
                        adopted = await self._adopt(session, spec)
                    elif not await _cancelled_for(session, spec):
                        created = await self._create(session, spec, None, first=serving is None)
                    building = await _one(session, Status.BUILDING)
        dropped = await self.drop_expired()
        return Reconciled(building is not None, created, adopted, cancelled, dropped)

    async def start(self, actor_id: UUID | None) -> int:
        """Re-embed with the active model even if it is the serving one (``reindex``, admin
        "rebuild"). Cancels a build in progress. Returns the new generation's number."""
        async with self._factory() as session, session.begin():
            await session.execute(_RECONCILE_LOCK)
            spec = await active_embed(session, self._llm)
            if spec is None:
                raise GenerationError("model_not_configured")
            building = await _one(session, Status.BUILDING)
            if building is not None:
                await self._cancel(session, building, actor_id, "restarted")
            serving = await _one(session, Status.SERVING)
            number = await self._create(session, spec, actor_id, first=serving is None)
        if number is None:
            raise GenerationError("embed_model_unavailable")  # not cleared, or down
        return number

    async def cancel(self, number: int, actor_id: UUID | None) -> None:
        """Abandon a build before the swap: the serving generation stays as it is."""
        async with self._factory() as session, session.begin():
            await session.execute(_RECONCILE_LOCK)
            row = await session.scalar(
                select(IndexGeneration).where(IndexGeneration.number == number)
            )
            if row is None:
                raise GenerationError("generation_not_found")
            if row.status != Status.BUILDING:
                raise GenerationError("generation_not_building")
            await self._cancel(session, row, actor_id, "admin")
        await self.drop_expired()

    async def _cancel(
        self, session: AsyncSession, row: IndexGeneration, actor_id: UUID | None, reason: str
    ) -> int:
        row.status = Status.CANCELLED
        row.retired_at = datetime.now(UTC)
        await AuditService(session).record(
            EventType.INDEX_GENERATION_CANCELLED,
            actor_id,
            AuditTarget("index_generation", str(row.number)),
            {"model": row.model, "reason": reason, "done": row.done, "total": row.total},
        )
        log.info("vector.generation_cancelled", generation=row.number, reason=reason)
        return row.number

    async def _probe(self, deployment: Deployment) -> int:
        vectors = await self._gateway.embed([PROBE_TEXT], private_data=True, deployment=deployment)
        if len(vectors) != 1 or not vectors[0]:
            raise GenerationError("probe_failed")
        return len(vectors[0])

    async def _usable(
        self, session: AsyncSession, spec: EmbedSpec
    ) -> tuple[Deployment, int] | None:
        deployment = await resolve_embed(
            session, spec.key, spec.deployment, self._cipher, self._llm
        )
        if deployment is None:
            log.warning("vector.generation_model_unusable", model=spec.model)
            return None
        try:
            return deployment, await self._probe(deployment)
        except (LLMError, GenerationError) as exc:
            # The model is down: try again on the next reconcile.
            log.warning(
                "vector.generation_probe_failed", model=spec.model, error=type(exc).__name__
            )
            return None

    async def _adopt(self, session: AsyncSession, spec: EmbedSpec) -> int | None:
        """The collections built before 10.3 become generation 1, if they match the model."""
        usable = await self._usable(session, spec)
        if usable is None:
            return None
        _, dimension = usable
        sizes = {name: await collection_size(self._client, name) for name in LEGACY}
        if any(size != dimension for size in sizes.values()):
            log.warning("vector.legacy_mismatch", sizes=sizes, dimension=dimension)
            return await self._create(session, spec, None, first=True)
        row = IndexGeneration(
            status=Status.SERVING,
            embed_key=spec.key,
            model=spec.model,
            deployment=spec.deployment,
            revision=spec.revision,
            dimension=dimension,
            private_collection=PRIVATE_COLLECTION,
            canonical_collection=CANONICAL_COLLECTION,
            cursor={"phase": "done"},
            serving_since=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        await ensure_collections(self._client, dimension, names=LEGACY)  # payload indexes
        log.info("vector.generation_adopted", generation=row.number, model=spec.model)
        return row.number

    async def _create(
        self, session: AsyncSession, spec: EmbedSpec, actor_id: UUID | None, *, first: bool
    ) -> int | None:
        usable = await self._usable(session, spec)
        if usable is None:
            return None
        _, dimension = usable
        row = IndexGeneration(
            status=Status.BUILDING,
            embed_key=spec.key,
            model=spec.model,
            deployment=spec.deployment,
            revision=spec.revision,
            dimension=dimension,
            private_collection="",
            canonical_collection="",
            cursor={"phase": "articles"},
            total=await count_items(self._factory),
        )
        session.add(row)
        await session.flush()
        row.private_collection = generation_collection(PRIVATE_COLLECTION, row.number)
        row.canonical_collection = generation_collection(CANONICAL_COLLECTION, row.number)
        await ensure_collections(
            self._client, dimension, names=(row.private_collection, row.canonical_collection)
        )
        await AuditService(session).record(
            EventType.INDEX_GENERATION_STARTED,
            actor_id,
            AuditTarget("index_generation", str(row.number)),
            {"model": spec.model, "dimension": dimension, "items": row.total, "first": first},
        )
        log.info(
            "vector.generation_started",
            generation=row.number,
            model=spec.model,
            dimension=dimension,
            items=row.total,
        )
        return row.number

    async def drop_expired(self) -> int:
        """Drop the collections of CANCELLED generations and of RETIRED ones past their grace
        period. The rows stay, as history."""
        async with self._factory() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(IndexGeneration)
                    .where(
                        IndexGeneration.dropped_at.is_(None),
                        or_(
                            IndexGeneration.status == Status.CANCELLED,
                            (IndexGeneration.status == Status.RETIRED)
                            & (IndexGeneration.drop_after <= func.now()),
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                names = tuple(n for n in (row.private_collection, row.canonical_collection) if n)
                await drop_collections(self._client, names)
                row.dropped_at = datetime.now(UTC)
                log.info("vector.generation_dropped", generation=row.number, collections=names)
            serving = await _one(session, Status.SERVING) if rows else None
            current = (
                (serving.private_collection, serving.canonical_collection) if serving else None
            )
        if current is not None:  # an adopted collection may have held the family name
            await point_aliases(self._client, current)
        return len(rows)

    async def swap(self, number: int) -> bool:
        """BUILDING ``number`` → SERVING; the serving one → RETIRED. False if it was cancelled."""
        grace = timedelta(hours=self._qdrant.retired_grace_hours)
        async with self._factory() as session, session.begin():
            await session.execute(_RECONCILE_LOCK)
            row = await _one(session, Status.BUILDING)
            if row is None or row.number != number:
                return False
            old = await _one(session, Status.SERVING)
            now = datetime.now(UTC)
            if old is not None:
                old.status = Status.RETIRED
                old.retired_at = now
                old.drop_after = now + grace
                await session.flush()  # one SERVING at a time (partial unique index)
            row.status = Status.SERVING
            row.serving_since = now
            row.cursor = {"phase": "done"}
            await AuditService(session).record(
                EventType.INDEX_GENERATION_SWAPPED,
                None,
                AuditTarget("index_generation", str(row.number)),
                {
                    "model": row.model,
                    "dimension": row.dimension,
                    "replaced": old.number if old is not None else None,
                    "done": row.done,
                    "failed": row.failed,
                },
            )
            names = (row.private_collection, row.canonical_collection)
        await point_aliases(self._client, names)
        log.info("vector.generation_swapped", generation=number)
        return True


async def _one(session: AsyncSession, status: IndexGenerationStatus) -> IndexGeneration | None:
    return await session.scalar(select(IndexGeneration).where(IndexGeneration.status == status))


async def _cancelled_for(session: AsyncSession, spec: EmbedSpec) -> bool:
    """An admin cancelled the build for this model under this revision: don't start it again
    until the configuration changes (or an admin restarts it)."""
    found = await session.scalar(
        select(IndexGeneration.id).where(
            IndexGeneration.status == Status.CANCELLED,
            IndexGeneration.embed_key == spec.key,
            IndexGeneration.revision == spec.revision,
        )
    )
    return found is not None


async def _has_legacy(client: AsyncQdrantClient) -> bool:
    real = {c.name for c in (await client.get_collections()).collections}
    return all(name in real for name in LEGACY)


# --- The build ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildRun:
    generation: int | None = None
    processed: int = 0
    more: bool = False  # stopped early (time budget): send the task again
    waiting: bool = False  # the model is down or gone: the next reconcile sends it again
    swapped: bool = False
    busy: bool = False  # another run holds the build lock


type Item = tuple[ArticleJob | DerivativeJob, dict[str, Any]]


class ReEmbedder:
    def __init__(
        self,
        factory: SessionFactory,
        store: GenerationStore,
        indexer: Indexer,
        manager: GenerationManager,
        llm: LlmSettings,
        on_swap: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._factory = factory
        self._store = store
        self._indexer = indexer
        self._manager = manager
        self._llm = llm
        self._on_swap = on_swap
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
            cursor: dict[str, Any] = dict(row.cursor or {"phase": "articles"})
            done, failed = row.done, row.failed
        if generation.deployment is None:
            return BuildRun(generation.number, waiting=True)
        throttle = 0.0 if generation.deployment.local else 60.0 / self._llm.reembed_per_minute
        deadline = self._clock() + budget_seconds
        processed = 0
        while True:
            items = await self._next(cursor)
            if not items:
                if not await self._save(generation, {"phase": "end"}, done, failed):
                    return BuildRun(generation.number, processed)
                swapped = await self._manager.swap(generation.number)
                self._store.forget()
                if swapped and self._on_swap is not None:
                    await self._on_swap()
                return BuildRun(generation.number, processed, swapped=swapped)
            for job, after in items:
                outcome = await self._index(job, generation)
                if outcome in (Outcome.UNAVAILABLE, Outcome.NOT_CONFIGURED):
                    await self._save(generation, cursor, done, failed)
                    return BuildRun(generation.number, processed, waiting=True)
                failed += outcome == Outcome.FAILED
                done += 1
                processed += 1
                cursor = after
                if processed % SAVE_EVERY == 0 and not await self._save(
                    generation, cursor, done, failed
                ):
                    return BuildRun(generation.number, processed)  # cancelled meanwhile
                if throttle:
                    await self._sleep(throttle)
                if self._clock() >= deadline:
                    if not await self._save(generation, cursor, done, failed):
                        return BuildRun(generation.number, processed)
                    return BuildRun(generation.number, processed, more=True)

    async def _index(self, job: ArticleJob | DerivativeJob, generation: Generation) -> Outcome:
        if isinstance(job, ArticleJob):
            return await self._indexer.canonical_article(job, [generation])
        return await self._indexer.private_derivative(job, [generation])

    async def _save(
        self, generation: Generation, cursor: dict[str, Any], done: int, failed: int
    ) -> bool:
        """Record progress; False if the generation is no longer being built."""
        async with self._factory() as session, session.begin():
            updated = await session.scalar(
                update(IndexGeneration)
                .where(
                    IndexGeneration.number == generation.number,
                    IndexGeneration.status == Status.BUILDING,
                )
                .values(cursor=cursor, done=done, failed=failed, updated_at=func.now())
                .returning(IndexGeneration.id)
            )
        return updated is not None

    async def _next(self, cursor: dict[str, Any]) -> list[Item]:
        """The next items after ``cursor``, each with the cursor that follows it."""
        phase = cursor.get("phase")
        if phase == "articles":
            after = cursor.get("after")
            async with self._factory() as session:
                query = select(WikiArticle.id).where(WikiArticle.status == ArticleStatus.CANONICAL)
                if after is not None:
                    query = query.where(WikiArticle.id > UUID(after))
                ids = list(await session.scalars(query.order_by(WikiArticle.id).limit(BATCH)))
            if ids:
                return [
                    (ArticleJob(article_id=i), {"phase": "articles", "after": str(i)}) for i in ids
                ]
            cursor = {"phase": "notes"}
            phase = "notes"
        if phase == "notes":
            user = cursor.get("user")
            after = cursor.get("after")
            while True:
                if user is None:
                    user = await self._next_user(None)
                    after = None
                    if user is None:
                        return []
                owner = UUID(str(user))
                async with rls_session(self._factory, owner) as session:
                    query = select(NoteDerivative.id).where(
                        NoteDerivative.status == DerivativeStatus.DONE
                    )
                    if after is not None:
                        query = query.where(NoteDerivative.id > UUID(str(after)))
                    ids = list(
                        await session.scalars(query.order_by(NoteDerivative.id).limit(BATCH))
                    )
                if ids:
                    return [
                        (
                            DerivativeJob(derivative_id=i, owner_id=owner),
                            {"phase": "notes", "user": str(owner), "after": str(i)},
                        )
                        for i in ids
                    ]
                user = await self._next_user(owner)
                after = None
                if user is None:
                    return []
        return []

    async def _next_user(self, after: UUID | None) -> UUID | None:
        async with self._factory() as session:
            query = select(User.id)
            if after is not None:
                query = query.where(User.id > after)
            return await session.scalar(query.order_by(User.id).limit(1))


async def generations_overview(factory: SessionFactory) -> list[IndexGeneration]:
    """Every generation, newest first (admin)."""
    async with factory() as session:
        return list(
            await session.scalars(
                select(IndexGeneration).order_by(IndexGeneration.number.desc()).limit(20)
            )
        )


__all__ = [
    "BuildRun",
    "GenerationError",
    "GenerationManager",
    "ReEmbedder",
    "Reconciled",
    "count_items",
    "generation_of",
    "generations_overview",
]
