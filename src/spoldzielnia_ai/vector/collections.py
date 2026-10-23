"""The collections and their payload indexes, created idempotently (4.1, 10.3).

Payloads hold identifiers and governance metadata only, never text (overview §2.5).

- ``private_notes_index``: ``user_id`` is a tenant index (``is_tenant``), and the global HNSW
  graph is off (``m=0``, ``payload_m=16``), so the graph is built per tenant. A search
  without the tenant filter can't use an index (``PrivateVectorRepo`` always adds it anyway).
- ``canonical_wiki_index``: every field the mandatory filter and the consent operations use.
- ``atlas_visual_index`` (9.8): image embeddings of approved atlas photos and their marks, with
  the provenance and governance fields the mandatory filter needs. Versioned like the text
  collections since 10.6 (``atlas.generations``), with the image model's probed dimension.

The two text collections are versioned since 10.3 (``vector.generations``): generation ``n``
lives in ``<family>__g<n>``, created when the generation is, with the dimension its embedding
model produced. The family name is kept as a Qdrant alias of the serving generation, for
operators only: the application always addresses a generation's collections by name, because
a query must be embedded with that generation's model (P8). Collections built before 10.3 keep
the bare family name and are adopted as the first generation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qdrant_client import models

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

log = get_logger(__name__)

PRIVATE_COLLECTION = "private_notes_index"
CANONICAL_COLLECTION = "canonical_wiki_index"

type IndexSchema = models.PayloadSchemaType | models.KeywordIndexParams

KEYWORD = models.PayloadSchemaType.KEYWORD

PRIVATE_INDEXES: dict[str, IndexSchema] = {
    "user_id": models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
    "note_id": KEYWORD,
    "derivative_id": KEYWORD,
}

CANONICAL_INDEXES: dict[str, IndexSchema] = {
    "block_id": KEYWORD,
    "article_id": KEYWORD,
    "author_id": KEYWORD,
    "consent_status": KEYWORD,
    "is_active": models.PayloadSchemaType.BOOL,
    "article_status": KEYWORD,
    "is_honeypot": models.PayloadSchemaType.BOOL,
    "verified_at": models.PayloadSchemaType.DATETIME,
}

COLLECTIONS: dict[str, dict[str, IndexSchema]] = {
    PRIVATE_COLLECTION: PRIVATE_INDEXES,
    CANONICAL_COLLECTION: CANONICAL_INDEXES,
}

ATLAS_COLLECTION = "atlas_visual_index"
ATLAS_INDEXES: dict[str, IndexSchema] = {
    "atlas_image_id": KEYWORD,
    "region_id": KEYWORD,
    "label_id": KEYWORD,
    "domain_id": KEYWORD,
    "author_id": KEYWORD,
    "consent_status": KEYWORD,
    "is_active": models.PayloadSchemaType.BOOL,
    "kind": KEYWORD,
    "verified_at": models.PayloadSchemaType.DATETIME,
}
INDEXES: dict[str, dict[str, IndexSchema]] = {**COLLECTIONS, ATLAS_COLLECTION: ATLAS_INDEXES}


class VectorSizeMismatch(RuntimeError):
    """An existing collection was created for another embedding model."""


def generation_collection(family: str, number: int) -> str:
    """``private_notes_index__g3``: the collection of ``family`` in generation ``number``."""
    return f"{family}__g{number}"


def family_of(name: str) -> str:
    return name.split("__g", 1)[0]


def _hnsw(family: str) -> models.HnswConfigDiff | None:
    return models.HnswConfigDiff(m=0, payload_m=16) if family == PRIVATE_COLLECTION else None


def _size(info: models.CollectionInfo) -> int | None:
    vectors = info.config.params.vectors
    return vectors.size if isinstance(vectors, models.VectorParams) else None


async def collection_size(client: AsyncQdrantClient, name: str) -> int | None:
    """The vector size of an existing collection (``None`` if there is no such collection)."""
    if not await client.collection_exists(name):
        return None
    return _size(await client.get_collection(name))


async def ensure_collection(client: AsyncQdrantClient, name: str, vector_size: int) -> bool:
    """Create ``name`` if missing and add any missing payload index. Returns True if created.
    Raises ``VectorSizeMismatch`` if it exists with another dimension."""
    family = family_of(name)
    created = False
    if not await client.collection_exists(name):
        await client.create_collection(
            name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
            hnsw_config=_hnsw(family),
        )
        created = True
    info = await client.get_collection(name)
    if (size := _size(info)) != vector_size:
        raise VectorSizeMismatch(f"{name} has vector size {size}, expected {vector_size}")
    for field, schema in INDEXES[family].items():
        if field not in info.payload_schema:
            await client.create_payload_index(name, field, field_schema=schema, wait=True)
    log.info("vector.collection_ready", collection=name, created=created, size=vector_size)
    return created


async def ensure_collections(
    client: AsyncQdrantClient,
    vector_size: int,
    names: tuple[str, str] = (PRIVATE_COLLECTION, CANONICAL_COLLECTION),
) -> dict[str, bool]:
    """The text collections ``names`` (private, canonical)."""
    return {name: await ensure_collection(client, name, vector_size) for name in names}


async def recreate_atlas_collection(
    client: AsyncQdrantClient, image_vector_size: int, name: str = ATLAS_COLLECTION
) -> None:
    """Drop and recreate an atlas collection (tests)."""
    if await client.collection_exists(name):
        await client.delete_collection(name)
    await ensure_collection(client, name, image_vector_size)


async def recreate_collections(
    client: AsyncQdrantClient,
    vector_size: int,
    names: tuple[str, str] = (PRIVATE_COLLECTION, CANONICAL_COLLECTION),
) -> None:
    """Drop and recreate the text collections ``names`` (tests)."""
    for name in names:
        if await client.collection_exists(name):
            await client.delete_collection(name)
    await ensure_collections(client, vector_size, names=names)


async def drop_collections(client: AsyncQdrantClient, names: tuple[str, ...]) -> None:
    for name in names:
        if await client.collection_exists(name):
            await client.delete_collection(name)


async def point_aliases(client: AsyncQdrantClient, names: tuple[str, ...]) -> None:
    """Make each family name an alias of ``names`` (the serving generation), unless the family
    name is itself a collection (adopted from before 10.3). For operators; best effort."""
    # Not ``collection_exists``: it may resolve an alias too.
    real = {c.name for c in (await client.get_collections()).collections}
    actions: list[models.AliasOperations] = []
    for name in names:
        family = family_of(name)
        if name == family or family in real:
            continue
        actions.append(
            models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=family))
        )
        actions.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(collection_name=name, alias_name=family)
            )
        )
    if not actions:
        return
    try:
        await client.update_collection_aliases(change_aliases_operations=actions)
    except Exception as exc:  # deleting a missing alias fails on some versions: retry plainly
        log.info("vector.alias_retry", error=type(exc).__name__)
        creates = [a for a in actions if isinstance(a, models.CreateAliasOperation)]
        try:
            await client.update_collection_aliases(change_aliases_operations=creates)
        except Exception as again:
            log.warning("vector.alias_failed", error=type(again).__name__)
