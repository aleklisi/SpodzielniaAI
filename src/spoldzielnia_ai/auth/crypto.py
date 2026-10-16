"""Keys derived from ``security.session_secret``: token encryption at rest and CSRF tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_BYTES = 12


def derive_key(secret: str, purpose: str) -> bytes:
    """A 256-bit key for one ``purpose``, so no two uses ever share key material."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"spoldzielnia-ai",
        info=f"spoldzielnia/{purpose}".encode(),
    )
    return hkdf.derive(secret.encode())


def digest(value: str) -> str:
    """Hex SHA-256: Redis keys for high-entropy secrets (session ids, refresh tokens)."""
    return hashlib.sha256(value.encode()).hexdigest()


class TokenSealer:
    """AES-256-GCM for IdP tokens kept in Redis. ``context`` is bound as associated data, so a
    sealed token copied into another token family's record does not open."""

    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(key)

    def seal(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return base64.urlsafe_b64encode(nonce + sealed).decode()

    def open(self, sealed: str, context: str) -> str | None:
        try:
            raw = base64.urlsafe_b64decode(sealed.encode())
            plaintext = self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode())
        except (InvalidTag, ValueError):
            return None
        return plaintext.decode()


class CsrfTokens:
    """Double-submit CSRF tokens bound to the session: ``HMAC(key, sid)``. Nothing is stored,
    and a token planted for one session is useless for another."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def for_session(self, sid: str) -> str:
        mac = hmac.new(self._key, sid.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    def matches(self, sid: str, token: str) -> bool:
        return hmac.compare_digest(self.for_session(sid), token)
