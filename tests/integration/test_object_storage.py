"""Chunk 2.1: ``S3ObjectStorage`` against the dev stack's MinIO (loopback port 9000)."""

from __future__ import annotations

import base64
import io
import os
from uuid import UUID

import pytest

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider, row_aad
from spoldzielnia_ai.core.settings import S3Settings
from spoldzielnia_ai.files.storage import S3ObjectStorage, new_object_key
from tests.integration.stack import require_port, secret

pytestmark = pytest.mark.integration


@pytest.fixture
def storage() -> S3ObjectStorage:
    require_port(9000)
    from pydantic import SecretStr

    return S3ObjectStorage(
        S3Settings(
            endpoint_url="http://127.0.0.1:9000",
            access_key="spoldzielnia",
            secret_key=SecretStr(secret("minio_root_password")),
        )
    )


async def test_encrypted_object_round_trip(storage: S3ObjectStorage) -> None:
    cipher = EnvelopeCipher(StaticKeyProvider(base64.b64encode(os.urandom(32)).decode()))
    aad = row_aad("note_attachment", UUID(int=1), UUID(int=2))
    key = cipher.new_key(aad)
    plaintext = os.urandom(3 * 1024 * 1024 + 7)
    sealed = io.BytesIO()
    length = cipher.encrypt_stream(key.key, io.BytesIO(plaintext), sealed, aad)
    sealed.seek(0)

    object_key = new_object_key()
    assert object_key.startswith("att/")
    assert len(object_key) == 36
    await storage.put(object_key, sealed, length)
    try:
        fetched = io.BytesIO()
        await storage.get(object_key, fetched)
        assert fetched.getvalue() == sealed.getvalue()
        fetched.seek(0)
        assert b"".join(cipher.decrypt_stream(key.key, fetched, aad)) == plaintext
    finally:
        await storage.delete(object_key)

    from minio.error import S3Error

    with pytest.raises(S3Error):
        await storage.get(object_key, io.BytesIO())
