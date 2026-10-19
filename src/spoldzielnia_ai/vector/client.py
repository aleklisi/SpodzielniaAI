"""Qdrant client over HTTPS with the API key, TLS verified against the internal CA (4.1)."""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING

from qdrant_client import AsyncQdrantClient

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import QdrantSettings


def make_qdrant(settings: QdrantSettings) -> AsyncQdrantClient:
    """REST client. Creating it opens no connection, and the server version is not probed."""
    verify: ssl.SSLContext | bool = True
    if settings.ca_bundle:
        verify = ssl.create_default_context(cafile=settings.ca_bundle)
    return AsyncQdrantClient(
        url=settings.url,
        api_key=settings.api_key.get_secret_value() if settings.api_key else None,
        timeout=settings.timeout_seconds,
        prefer_grpc=False,
        check_compatibility=False,
        verify=verify,  # passed through to httpx
    )
