"""Chunk 5.4: canary signatures, canary text, placement between clusters, decaying risk."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest
from fakeredis import FakeAsyncRedis
from pydantic import SecretStr

from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.shield.honeypot import (
    SIGNATURE,
    TOPICS,
    HoneypotKeyMissing,
    betweenness,
    canary_text,
    find_signatures,
    make_signature,
    signing_key,
)
from spoldzielnia_ai.shield.risk import RiskStore

KEY = b"0123456789abcdef0123456789abcdef"


def test_signatures_verify_without_a_database() -> None:
    signature = make_signature(KEY, "PSZ", 2026)
    assert SIGNATURE.fullmatch(signature)
    text = f"…zgodnie z procedurą {signature.lower()} oraz {signature}."

    assert find_signatures(text, KEY) == [signature]  # case-insensitive, no repeats
    assert find_signatures(text, b"another-key-another-key-another!") == []


def test_forged_signature_is_rejected() -> None:
    signature = make_signature(KEY, "PSZ", 2026)
    nonce_end = len("PSZ-") + 4
    forged = signature[:nonce_end] + ("AAAA" if signature[nonce_end:-5] != "AAAA" else "BBBB")
    assert find_signatures(f"{forged}/2026", KEY) == []
    assert find_signatures("PSZ-ABCDEFGH/2026", KEY) == []


def test_canary_text_is_plausible_and_mixes_topics() -> None:
    signature = make_signature(KEY, "PSZ", 2026)
    text = canary_text(signature, datetime(2026, 10, 19, tzinfo=UTC))
    assert text.startswith(f"Procedura wewnętrzna {signature} dotycząca ")
    assert sum(topic in text for topic in TOPICS) == 3


def test_betweenness() -> None:
    rng = np.random.default_rng(7)
    centroids = rng.normal(size=(5, 32)).astype(np.float32)
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    inside = betweenness(centroids[0], centroids)
    between = betweenness((centroids[0] + centroids[1]) / 2, centroids)
    assert between > 0.95
    assert inside < 0.6


def test_key_is_required() -> None:
    with pytest.raises(HoneypotKeyMissing):
        signing_key(ShieldSettings())
    with pytest.raises(HoneypotKeyMissing):
        signing_key(ShieldSettings(honeypot_key=SecretStr("  \n")))
    assert signing_key(ShieldSettings(honeypot_key=SecretStr("k\n"))) == b"k"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


async def test_risk_points_decay_with_the_half_life() -> None:
    clock = Clock()
    risk = RiskStore(FakeAsyncRedis(), ShieldSettings(risk_half_life_days=7), clock=clock)
    user = uuid4()

    assert await risk.incidents(user) == 0.0
    assert await risk.add(user, 0.5) == 0.5
    clock.now += 7 * 86400
    assert await risk.incidents(user) == pytest.approx(0.25)
    assert await risk.add(user, 0.5) == pytest.approx(0.75)
    assert await risk.add(user, 0.9) == 1.0  # capped
    assert await risk.incidents(uuid4()) == 0.0
