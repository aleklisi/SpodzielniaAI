"""Generations of the text index: which embedding model built which collections (10.3, P8).

A generation is one pair of collections (private notes, canonical wiki) built by one embedding
model, recorded in ``index_generation`` with the model's deployment (no secrets) and the
dimension it produced. Postgres is the source of truth; Qdrant aliases are a convenience.

- **Searching** uses the SERVING generation, and the query is embedded with *that
  generation's* model (``Generation.deployment``), never simply with whatever EMBED is routed
  to: after an admin switches the embedding model, the old generation keeps serving, with the
  old model, until the new one is complete. No query ever mixes two vector spaces (P8).
- **Indexing** writes to every *writable* generation (SERVING and BUILDING), each with its own
  model, so a generation being built never misses a change made during the build.
- **Consent and governance changes** (the outbox) go to every *live* generation, which also
  includes a RETIRED one until it is dropped: a process that still has the old generation
  cached for a few seconds after the swap must not find revoked content there.

A generation's model is resolved like an ACTIVE configuration's (``config_store.resolve``):
the provider row is read live, its endpoint classified again and its clearance re-checked, so
un-clearing a provider stops its generation at once. A model that can no longer be used gives
``deployment = None``, and embedding with it raises ``ModelNotConfigured``: never another model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import or_, select

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import IndexGenerationStatus, LlmRevisionStatus, LlmRoutePurpose
from spoldzielnia_ai.db.models import IndexGeneration, LlmConfigRevision, LlmProvider
from spoldzielnia_ai.db.models.index_generation import TEXT
from spoldzielnia_ai.llm.config_store import resolve
from spoldzielnia_ai.llm.gateway import LOCAL_EMBED, LOCAL_IMAGE_EMBED, LLMPurpose, env_config
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot, SnapshotDeployment

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import Deployment

log = get_logger(__name__)

Status = IndexGenerationStatus
WRITABLE = (Status.SERVING, Status.BUILDING)


def embed_key(base_url: str, provider: str, model: str) -> str:
    """What identifies an embedding model: the same key means the same vector space."""
    return f"{provider}/{model}@{base_url.rstrip('/')}"


@dataclass(frozen=True, slots=True)
class Generation:
    number: int
    status: IndexGenerationStatus
    model: str
    dimension: int
    private_collection: str
    canonical_collection: str
    # The model that built it, if it can be used now (``None``: not, so nothing is embedded).
    deployment: Deployment | None = None

    @property
    def collections(self) -> tuple[str, str]:
        return (self.private_collection, self.canonical_collection)


class GenerationSource(Protocol):
    async def serving(self) -> Generation | None: ...

    async def writable(self) -> list[Generation]: ...

    async def live(self) -> list[Generation]: ...


@dataclass(frozen=True, slots=True)
class StaticGenerations:
    """Fixed generations (tests, one-off tools): the first SERVING one serves."""

    generations: tuple[Generation, ...]

    async def serving(self) -> Generation | None:
        return next((g for g in self.generations if g.status == Status.SERVING), None)

    async def writable(self) -> list[Generation]:
        return [g for g in self.generations if g.status in WRITABLE]

    async def live(self) -> list[Generation]:
        return [g for g in self.generations if g.status != Status.CANCELLED]


@dataclass(frozen=True, slots=True)
class EmbedSpec:
    """The embedding model the ACTIVE configuration routes EMBED to (or ``.env``'s)."""

    key: str
    model: str
    deployment: dict[str, Any] | None  # SnapshotDeployment as JSON; None: the ``.env`` model
    revision: int


async def active_embed(session: AsyncSession, settings: LlmSettings) -> EmbedSpec | None:
    if settings.config_source == "db":
        row = (
            await session.execute(
                select(LlmConfigRevision.number, LlmConfigRevision.snapshot).where(
                    LlmConfigRevision.status == LlmRevisionStatus.ACTIVE
                )
            )
        ).first()
        if row is not None and row.snapshot is not None:
            item = ConfigSnapshot.model_validate(row.snapshot).deployment_for(LlmRoutePurpose.EMBED)
            if item is None:
                return None
            return EmbedSpec(
                embed_key(item.base_url, item.litellm_provider, item.model),
                item.display_name,
                item.model_dump(mode="json"),
                row.number,
            )
    if not settings.embed_model:
        return None
    return EmbedSpec(
        embed_key(settings.local_base_url, "openai", settings.embed_model),
        settings.embed_model,
        None,
        0,
    )


async def resolve_embed(
    session: AsyncSession,
    key: str,
    deployment: dict[str, Any] | None,
    cipher: EnvelopeCipher,
    settings: LlmSettings,
    purpose: LLMPurpose = LLMPurpose.EMBED,
) -> Deployment | None:
    """The usable deployment of an embedding model, or ``None`` (see the module docstring).
    ``purpose`` is EMBED, or IMAGE_EMBED for the atlas (10.6)."""
    if deployment is None:
        name = LOCAL_IMAGE_EMBED if purpose == LLMPurpose.IMAGE_EMBED else LOCAL_EMBED
        env = env_config(settings).deployments.get(name)
        if env is None or embed_key(env.base_url, env.provider, env.model) != key:
            return None
        return env if env.cleared else None
    item = SnapshotDeployment.model_validate(deployment)
    provider = await session.get(LlmProvider, item.provider_id)
    if provider is None or provider.disabled_at is not None or provider.base_url != item.base_url:
        return None
    route = LlmRoutePurpose(purpose.value)
    snapshot = ConfigSnapshot(routes={route: "embed"}, deployments={"embed": item})
    config = resolve(-1, snapshot, {provider.id: provider}, cipher, settings)
    name = config.routes.get(purpose)
    found = config.deployments.get(name) if name is not None else None
    # Embeddings are private data (rule 4): an un-cleared provider's generation has no model
    # here already, not only in the gateway.
    return found if found is not None and found.cleared else None


class GenerationStore:
    """``GenerationSource`` over ``index_generation``. ``serving()`` is cached for
    ``ttl_seconds`` (the read path of every search); writers always read fresh, so a generation
    created a moment ago never misses a write. A cached generation stays correct after a swap:
    it carries its own model and collections, which stay until the grace period ends."""

    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        settings: LlmSettings,
        ttl_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._settings = settings
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: tuple[float, Generation | None] | None = None

    def forget(self) -> None:
        self._cached = None

    async def serving(self) -> Generation | None:
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        found = await self._load(IndexGeneration.status == Status.SERVING, resolve_model=True)
        serving = found[0] if found else None
        self._cached = (now, serving)
        return serving

    async def writable(self) -> list[Generation]:
        return await self._load(IndexGeneration.status.in_(WRITABLE), resolve_model=True)

    async def live(self) -> list[Generation]:
        """Every generation whose collections still exist and may be read."""
        return await self._load(
            or_(
                IndexGeneration.status.in_(WRITABLE),
                (IndexGeneration.status == Status.RETIRED) & IndexGeneration.dropped_at.is_(None),
            ),
            resolve_model=False,
        )

    async def _load(self, where: Any, *, resolve_model: bool) -> list[Generation]:
        async with self._factory() as session:
            rows = list(
                await session.scalars(
                    select(IndexGeneration)
                    .where(where, IndexGeneration.family == TEXT)
                    .order_by(IndexGeneration.number)
                )
            )
            return [
                await self.generation(session, row, resolve_model=resolve_model) for row in rows
            ]

    async def generation(
        self, session: AsyncSession, row: IndexGeneration, *, resolve_model: bool = True
    ) -> Generation:
        deployment = None
        if resolve_model:
            deployment = await resolve_embed(
                session, row.embed_key, row.deployment, self._cipher, self._settings
            )
            if deployment is None:
                log.warning("vector.generation_model_unusable", generation=row.number)
        return generation_of(row, deployment)


def generation_of(row: IndexGeneration, deployment: Deployment | None = None) -> Generation:
    return Generation(
        number=row.number,
        status=row.status,
        model=row.model,
        dimension=row.dimension,
        private_collection=row.private_collection,
        canonical_collection=row.canonical_collection,
        deployment=deployment,
    )


def collections_of(generations: Iterable[Generation]) -> list[str]:
    return [name for g in generations for name in g.collections]
