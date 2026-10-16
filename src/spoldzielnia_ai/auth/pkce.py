"""Random values for one login attempt: PKCE (RFC 7636), ``state`` and ``nonce``."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass

# 32 random bytes, base64url without padding: 43 characters.
_TOKEN_BYTES = 32
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def code_challenge_s256(code_verifier: str) -> str:
    return _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())


@dataclass(frozen=True, slots=True)
class LoginSecrets:
    state: str
    nonce: str
    code_verifier: str  # 86 chars of the unreserved set, within the 43-128 RFC 7636 bounds

    @property
    def code_challenge(self) -> str:
        return code_challenge_s256(self.code_verifier)

    @classmethod
    def generate(cls) -> LoginSecrets:
        return cls(
            state=secrets.token_urlsafe(_TOKEN_BYTES),
            nonce=secrets.token_urlsafe(_TOKEN_BYTES),
            code_verifier=secrets.token_urlsafe(64),
        )
