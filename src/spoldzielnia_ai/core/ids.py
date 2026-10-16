"""Identifier generation."""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_TS_MASK = (1 << 48) - 1


def uuid7() -> UUID:
    """Time-ordered UUID version 7 (RFC 9562 §5.7).

    48-bit Unix milliseconds, then 74 random bits. Keys created later sort later, which keeps
    B-tree inserts local. Python 3.12 has no ``uuid.uuid7`` and Postgres 16 no ``uuidv7()``,
    so ids are generated in the application.
    """
    ms = time.time_ns() // 1_000_000
    value = (
        (ms & _TS_MASK) << 80
        | 0x7 << 76  # version
        | secrets.randbits(12) << 64  # rand_a
        | 0b10 << 62  # RFC 9562 variant
        | secrets.randbits(62)  # rand_b
    )
    return UUID(int=value)
