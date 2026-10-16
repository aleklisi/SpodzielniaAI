"""Chunk 2.1: envelope encryption (``core.crypto``)."""

from __future__ import annotations

import base64
import io
import os
from uuid import UUID

import pytest

from spoldzielnia_ai.core.crypto import (
    SEGMENT_SIZE,
    EnvelopeCipher,
    InvalidTag,
    StaticKeyProvider,
    row_aad,
)

KEK = base64.b64encode(b"k" * 32).decode()
OTHER_KEK = base64.b64encode(b"o" * 32).decode()
ROW, OTHER_ROW = UUID(int=1), UUID(int=2)
OWNER, OTHER_OWNER = UUID(int=10), UUID(int=11)
AAD = row_aad("private_note", ROW, OWNER)


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(StaticKeyProvider(KEK))


def test_field_round_trip(cipher: EnvelopeCipher) -> None:
    key = cipher.new_key(AAD)
    blob = cipher.encrypt_field(key.key, b"Warroza: kwas szczawiowy", AAD, "body")
    assert b"Warroza" not in blob
    dek = cipher.unwrap(key.wrapped, AAD)
    assert cipher.decrypt_field(dek, blob, AAD, "body").decode() == "Warroza: kwas szczawiowy"


@pytest.mark.parametrize(
    ("aad", "field"),
    [
        (row_aad("private_note", OTHER_ROW, OWNER), "body"),  # copied to another row
        (row_aad("private_note", ROW, OTHER_OWNER), "body"),  # copied to another user's row
        (row_aad("note_attachment", ROW, OWNER), "body"),  # another table
        (AAD, "title"),  # swapped between fields
    ],
    ids=["row", "owner", "table", "field"],
)
def test_ciphertext_is_bound_to_its_row(cipher: EnvelopeCipher, aad: str, field: str) -> None:
    key = cipher.new_key(AAD)
    blob = cipher.encrypt_field(key.key, b"secret", AAD, "body")
    with pytest.raises(InvalidTag):
        cipher.decrypt_field(key.key, blob, aad, field)
    if aad != AAD:  # the DEK is bound to its row (not to a field)
        with pytest.raises(InvalidTag):
            cipher.unwrap(key.wrapped, aad)


def test_tampered_field_fails(cipher: EnvelopeCipher) -> None:
    key = cipher.new_key(AAD)
    blob = bytearray(cipher.encrypt_field(key.key, b"secret", AAD, "body"))
    blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        cipher.decrypt_field(key.key, bytes(blob), AAD, "body")
    with pytest.raises(InvalidTag):
        cipher.decrypt_field(key.key, b"short", AAD, "body")


def test_kek_rotation_keeps_old_keys_readable() -> None:
    old = EnvelopeCipher(StaticKeyProvider(OTHER_KEK))
    wrapped = old.new_key(AAD).wrapped
    rotated = EnvelopeCipher(StaticKeyProvider(KEK, previous_b64=(OTHER_KEK,)))
    assert len(rotated.unwrap(wrapped, AAD)) == 32
    with pytest.raises(InvalidTag):  # without the previous key: unknown KEK id
        EnvelopeCipher(StaticKeyProvider(KEK)).unwrap(wrapped, AAD)


@pytest.mark.parametrize("bad", ["", base64.b64encode(b"short").decode(), "not-base64!"])
def test_kek_must_be_32_bytes_base64(bad: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - binascii.Error is a ValueError too
        StaticKeyProvider(bad)


def _encrypt(cipher: EnvelopeCipher, data: bytes) -> tuple[bytes, bytes]:
    key = cipher.new_key(AAD)
    out = io.BytesIO()
    written = cipher.encrypt_stream(key.key, io.BytesIO(data), out, AAD)
    assert written == len(out.getvalue())
    return key.key, out.getvalue()


def _decrypt(cipher: EnvelopeCipher, dek: bytes, blob: bytes, aad: str = AAD) -> bytes:
    return b"".join(cipher.decrypt_stream(dek, io.BytesIO(blob), aad))


@pytest.mark.parametrize("size", [0, 1, SEGMENT_SIZE, SEGMENT_SIZE + 1, 2 * SEGMENT_SIZE + 123])
def test_stream_round_trip(cipher: EnvelopeCipher, size: int) -> None:
    data = os.urandom(size)
    dek, blob = _encrypt(cipher, data)
    assert _decrypt(cipher, dek, blob) == data


SEG = SEGMENT_SIZE + 16  # ciphertext segment incl. tag
HEADER = 9


@pytest.mark.parametrize("attack", ["truncate", "reorder", "append", "flip", "other-row"])
def test_stream_tampering_fails(cipher: EnvelopeCipher, attack: str) -> None:
    dek, blob = _encrypt(cipher, os.urandom(3 * SEGMENT_SIZE))
    header, body = blob[:HEADER], blob[HEADER:]
    segments = [body[i : i + SEG] for i in range(0, len(body), SEG)]
    aad = AAD
    match attack:
        case "truncate":  # drop the final segment: the new last one is not marked final
            blob = header + b"".join(segments[:-1])
        case "reorder":
            blob = header + segments[1] + segments[0] + b"".join(segments[2:])
        case "append":
            blob = blob + segments[0]
        case "flip":
            mutable = bytearray(blob)
            mutable[HEADER + 5] ^= 1
            blob = bytes(mutable)
        case "other-row":
            aad = row_aad("private_note", OTHER_ROW, OWNER)
        case _:
            raise AssertionError(attack)
    with pytest.raises(InvalidTag):
        _decrypt(cipher, dek, blob, aad)
