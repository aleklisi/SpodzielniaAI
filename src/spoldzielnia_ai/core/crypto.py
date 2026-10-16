"""Envelope encryption for the private zone (gap G4).

Every encrypted row or object has its own random 256-bit data key (DEK). The DEK is wrapped by
the key-encryption key (KEK) from a ``KeyProvider``. Ciphertexts and wrapped keys are bound to
their row through the associated data ``row_aad(table, row_id, owner_id)``: a ciphertext copied
into another row, or another user's row, fails authentication (``InvalidTag``).

Formats (all AES-256-GCM):

- Wrapped DEK: ``0x01 | len(kek_id) | kek_id | nonce(12) | GCM(KEK, DEK, "dek|" + aad)``.
  The KEK id lets old keys be unwrapped after a KEK rotation.
- Field blob: ``nonce(12) | GCM(DEK, plaintext, aad + "|" + field)``.
- Object stream (attachments, up to hundreds of MB): segmented AEAD. 1 MiB plaintext segments,
  nonce = random 8-byte prefix ‖ 32-bit counter, and the segment index and a final flag in the
  AAD, so reordered, dropped, truncated or extended streams fail. Layout:
  ``0x01 | prefix(8) | (segment ciphertext + tag)…``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Protocol
from uuid import UUID  # noqa: TC003 - used in signatures at runtime by callers

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["InvalidTag"]

_VERSION = 1
_NONCE = 12
_TAG = 16
SEGMENT_SIZE = 1024 * 1024
_PREFIX = 8


def row_aad(table: str, row_id: UUID, owner_id: UUID) -> str:
    return f"{table}:{row_id}:{owner_id}"


class KeyProvider(Protocol):
    """Source of key-encryption keys. ``current`` wraps new DEKs, ``get`` unwraps old ones."""

    def current(self) -> tuple[str, bytes]: ...

    def get(self, kek_id: str) -> bytes: ...


class StaticKeyProvider:
    """One KEK from configuration (the Docker secret ``kek``, base64 of 32 bytes).

    The KEK id is a fingerprint, so a rotated secret never silently unwraps old DEKs with the
    wrong key. A ``VaultTransitKeyProvider`` can replace this without changing stored formats.
    """

    def __init__(self, kek_b64: str, previous_b64: tuple[str, ...] = ()) -> None:
        self._keys: dict[str, bytes] = {}
        self._current = self._add(kek_b64)
        for old in previous_b64:
            self._add(old)

    def _add(self, b64: str) -> str:
        key = base64.b64decode(b64.strip(), validate=True)
        if len(key) != 32:
            raise ValueError("KEK must be 32 bytes (base64)")
        kek_id = "static:" + hashlib.sha256(b"kek-id|" + key).hexdigest()[:16]
        self._keys[kek_id] = key
        return kek_id

    def current(self) -> tuple[str, bytes]:
        return self._current, self._keys[self._current]

    def get(self, kek_id: str) -> bytes:
        try:
            return self._keys[kek_id]
        except KeyError:
            raise InvalidTag from None


@dataclass(frozen=True, slots=True)
class DataKey:
    """A plaintext DEK with its wrapped form. Keep only ``wrapped`` at rest."""

    key: bytes
    wrapped: bytes


class EnvelopeCipher:
    def __init__(self, keys: KeyProvider) -> None:
        self._keys = keys

    # --- Keys ---------------------------------------------------------------------------

    def new_key(self, aad: str) -> DataKey:
        dek = AESGCM.generate_key(bit_length=256)
        kek_id, kek = self._keys.current()
        nonce = os.urandom(_NONCE)
        wrapped = AESGCM(kek).encrypt(nonce, dek, f"dek|{aad}".encode())
        kid = kek_id.encode()
        return DataKey(dek, bytes([_VERSION, len(kid)]) + kid + nonce + wrapped)

    def unwrap(self, wrapped: bytes, aad: str) -> bytes:
        if len(wrapped) < 2 or wrapped[0] != _VERSION:
            raise InvalidTag
        kid_len = wrapped[1]
        kek_id = wrapped[2 : 2 + kid_len].decode(errors="replace")
        rest = wrapped[2 + kid_len :]
        kek = self._keys.get(kek_id)
        return AESGCM(kek).decrypt(rest[:_NONCE], rest[_NONCE:], f"dek|{aad}".encode())

    # --- Fields -------------------------------------------------------------------------

    @staticmethod
    def encrypt_field(dek: bytes, plaintext: bytes, aad: str, field: str) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + AESGCM(dek).encrypt(nonce, plaintext, f"{aad}|{field}".encode())

    @staticmethod
    def decrypt_field(dek: bytes, blob: bytes, aad: str, field: str) -> bytes:
        if len(blob) < _NONCE + _TAG:
            raise InvalidTag
        return AESGCM(dek).decrypt(blob[:_NONCE], blob[_NONCE:], f"{aad}|{field}".encode())

    # --- Streams ------------------------------------------------------------------------

    @staticmethod
    def _segment_aad(aad: str, index: int, final: bool) -> bytes:
        return f"{aad}|seg|{index}|{int(final)}".encode()

    @staticmethod
    def _nonce(prefix: bytes, index: int) -> bytes:
        return prefix + struct.pack(">I", index)

    def encrypt_stream(self, dek: bytes, src: IO[bytes], dst: IO[bytes], aad: str) -> int:
        """Encrypt ``src`` into ``dst``. Returns the number of ciphertext bytes written."""
        aead = AESGCM(dek)
        prefix = os.urandom(_PREFIX)
        written = dst.write(bytes([_VERSION]) + prefix)
        index = 0
        segment = src.read(SEGMENT_SIZE)
        while True:
            following = src.read(SEGMENT_SIZE)
            final = not following
            if index > 0xFFFFFFFF:
                raise ValueError("stream too long")
            sealed = aead.encrypt(
                self._nonce(prefix, index), segment, self._segment_aad(aad, index, final)
            )
            written += dst.write(sealed)
            if final:
                return written
            segment, index = following, index + 1

    def decrypt_stream(self, dek: bytes, src: IO[bytes], aad: str) -> Iterator[bytes]:
        """Yield plaintext segments. Raises ``InvalidTag`` on any tampering, including
        truncation (no final segment) or trailing data."""
        aead = AESGCM(dek)
        header = src.read(1 + _PREFIX)
        if len(header) != 1 + _PREFIX or header[0] != _VERSION:
            raise InvalidTag
        prefix = header[1:]
        index = 0
        chunk = src.read(SEGMENT_SIZE + _TAG)
        while True:
            following = src.read(SEGMENT_SIZE + _TAG)
            final = not following
            if len(chunk) < _TAG:
                raise InvalidTag
            yield aead.decrypt(
                self._nonce(prefix, index), chunk, self._segment_aad(aad, index, final)
            )
            if final:
                return
            chunk, index = following, index + 1
