"""The two collections and their payload indexes, created idempotently by ``bootstrap`` (4.1).

Payloads hold identifiers and governance metadata only, never text (overview §2.5).

- ``private_notes_index``: ``user_id`` is a tenant index (``is_tenant``), and the global HNSW
  graph is off (``m=0``, ``payload_m=16``), so the graph is built per tenant. A search
  without the tenant filter can't use an index (``PrivateVectorRepo`` always adds it anyway).
- ``canonical_wiki_index``: every field the mandatory filter and the consent operations use.
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


class VectorSizeMismatch(RuntimeError):
    """An existing collection was created for another embedding model."""


def _hnsw(name: str) -> models.HnswConfigDiff | None:
    return models.HnswConfigDiff(m=0, payload_m=16) if name == PRIVATE_COLLECTION else None


def _size(info: models.CollectionInfo) -> int | None:
    vectors = info.config.params.vectors
    return vectors.size if isinstance(vectors, models.VectorParams) else None


async def ensure_collection(client: AsyncQdrantClient, name: str, vector_size: int) -> bool:
    """Create ``name`` if missing and add any missing payload index. Returns True if created.
    Raises ``VectorSizeMismatch`` if it exists with another dimension."""
    created = False
    if not await client.collection_exists(name):
        await client.create_collection(
            name,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
            hnsw_config=_hnsw(name),
        )
        created = True
    info = await client.get_collection(name)
    if (size := _size(info)) != vector_size:
        raise VectorSizeMismatch(
            f"{name} has vector size {size}, settings say {vector_size}: run `reindex`"
        )
    for field, schema in COLLECTIONS[name].items():
        if field not in info.payload_schema:
            await client.create_payload_index(name, field, field_schema=schema, wait=True)
    log.info("bootstrap.collection_ready", collection=name, created=created, size=vector_size)
    return created


async def ensure_collections(client: AsyncQdrantClient, vector_size: int) -> dict[str, bool]:
    return {name: await ensure_collection(client, name, vector_size) for name in COLLECTIONS}


async def recreate_collections(client: AsyncQdrantClient, vector_size: int) -> None:
    """Drop and recreate both collections (``reindex``)."""
    for name in COLLECTIONS:
        if await client.collection_exists(name):
            await client.delete_collection(name)
    await ensure_collections(client, vector_size)
