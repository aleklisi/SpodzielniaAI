"""Idempotent provisioning of external resources, run by the one-shot ``bootstrap`` job.

1.2b: the private object-storage bucket with default server-side encryption (SSE-S3 via
MinIO KMS), versioning off, and no anonymous access.
4.1: the Qdrant atlas collection and its payload indexes (``vector.collections``), over
internal TLS. The text collections are created lazily, by the worker, for each index generation
with the dimension its embedding model produces (10.3): starting needs no embedding model.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from minio import Minio
from minio.sseconfig import Rule, SSEConfig

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.collections import ensure_atlas_collection

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import BootstrapSettings, S3Settings

log = get_logger(__name__)


class BucketClient(Protocol):
    """The subset of the MinIO client used here (lets tests use a fake)."""

    def bucket_exists(self, bucket_name: str) -> bool: ...

    def make_bucket(
        self, bucket_name: str, location: str | None = None, object_lock: bool = False
    ) -> None: ...

    def set_bucket_encryption(self, bucket_name: str, config: SSEConfig) -> None: ...

    def get_bucket_encryption(self, bucket_name: str) -> SSEConfig | None: ...


def make_s3_client(s3: S3Settings) -> Minio:
    if s3.secret_key is None:
        raise ValueError("s3.secret_key is required")
    url = urlsplit(s3.endpoint_url)
    if not url.hostname:
        raise ValueError(f"invalid s3.endpoint_url: {s3.endpoint_url!r}")
    endpoint = f"{url.hostname}:{url.port}" if url.port else url.hostname
    return Minio(
        endpoint,
        access_key=s3.access_key,
        secret_key=s3.secret_key.get_secret_value(),
        secure=url.scheme == "https",
        region=s3.region,
    )


def ensure_bucket(client: BucketClient, bucket: str, region: str) -> bool:
    """Create ``bucket`` if missing and enforce default SSE-S3. Returns True if created."""
    created = False
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket, location=region)
        created = True
    client.set_bucket_encryption(bucket, SSEConfig(Rule.new_sse_s3_rule()))
    log.info("bootstrap.bucket_ready", bucket=bucket, created=created, sse="SSE-S3")
    return created


async def _collections(settings: BootstrapSettings, qdrant: AsyncQdrantClient | None) -> None:
    client = qdrant or make_qdrant(settings.qdrant)
    try:
        await ensure_atlas_collection(client, settings.qdrant.image_vector_size)
    finally:
        if qdrant is None:
            await client.close()


def run_bootstrap(
    settings: BootstrapSettings,
    client: BucketClient | None = None,
    qdrant: AsyncQdrantClient | None = None,
) -> None:
    client = client or make_s3_client(settings.s3)
    ensure_bucket(client, settings.s3.bucket, settings.s3.region)
    asyncio.run(_collections(settings, qdrant))
