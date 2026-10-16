"""Object storage for encrypted attachments.

Objects are ciphertext produced by ``core.crypto`` before they leave the process. MinIO's default
SSE-S3 (bootstrap) is a second layer. Keys are random (``new_object_key``), never derived from
user input.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import IO, TYPE_CHECKING, BinaryIO, Protocol, cast

from spoldzielnia_ai.bootstrap import make_s3_client

if TYPE_CHECKING:
    from minio import Minio

    from spoldzielnia_ai.core.settings import S3Settings

_COPY_CHUNK = 1024 * 1024


def new_object_key() -> str:
    """``att/`` + 128 random bits. Random (not time-ordered) so keys leak nothing."""
    return f"att/{secrets.token_hex(16)}"


class ObjectStorage(Protocol):
    async def put(self, key: str, src: IO[bytes], length: int) -> None: ...

    async def get(self, key: str, dst: IO[bytes]) -> None: ...

    async def delete(self, key: str) -> None: ...


class S3ObjectStorage:
    """S3/MinIO via the synchronous ``minio`` client, run in worker threads."""

    def __init__(self, settings: S3Settings) -> None:
        self._settings = settings
        self._client: Minio | None = None

    def _minio(self) -> Minio:
        if self._client is None:  # lazily: only processes that store files hold the S3 secret
            self._client = make_s3_client(self._settings)
        return self._client

    async def put(self, key: str, src: IO[bytes], length: int) -> None:
        await asyncio.to_thread(
            self._minio().put_object,
            self._settings.bucket,
            key,
            cast("BinaryIO", src),  # minio only reads from it
            length,
            content_type="application/octet-stream",
        )

    async def get(self, key: str, dst: IO[bytes]) -> None:
        def download() -> None:
            response = self._minio().get_object(self._settings.bucket, key)
            try:
                for chunk in response.stream(_COPY_CHUNK):
                    dst.write(chunk)
            finally:
                response.close()
                response.release_conn()

        await asyncio.to_thread(download)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._minio().remove_object, self._settings.bucket, key)


class MemoryObjectStorage:
    """In-process storage for tests and local experiments."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, src: IO[bytes], length: int) -> None:
        data = src.read()
        if len(data) != length:
            raise ValueError("length mismatch")
        self.objects[key] = data

    async def get(self, key: str, dst: IO[bytes]) -> None:
        dst.write(self.objects[key])

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
