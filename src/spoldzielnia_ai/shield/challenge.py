"""Proof-of-work step-up (5.6): a hashcash puzzle the client solves to continue at HIGH risk.

``issue`` stores a random nonce for the account (``pow:{user}:{nonce}``, TTL
``pow_ttl_seconds``). The client finds a ``counter`` such that ``SHA-256(nonce:counter)``
starts with ``difficulty`` zero bits (~2^18 hashes by default: about a second in a browser)
and resends the request with ``X-Shield-PoW: nonce:counter``. ``redeem`` accepts a solution
once: the nonce is deleted atomically, so one solution lets exactly one request through.
A bot paying a second of CPU per query loses the scale that distillation needs, and every
puzzle solved within 24h makes the next one ``escalation_bits`` harder. Puzzles issued and not
yet solved are counted (``open``): the guard treats ignoring step-up as a risk signal.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from redis.asyncio import Redis

HEADER = "X-Shield-PoW"


def leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        return bits + (8 - byte.bit_length())
    return bits


def is_solution(nonce: str, counter: str, difficulty: int) -> bool:
    digest = hashlib.sha256(f"{nonce}:{counter}".encode()).digest()
    return leading_zero_bits(digest) >= difficulty


def solve(nonce: str, difficulty: int) -> str:
    """Reference solver (clients implement the same loop; tests use this)."""
    counter = 0
    while not is_solution(nonce, str(counter), difficulty):
        counter += 1
    return str(counter)


@dataclass(frozen=True, slots=True)
class Puzzle:
    nonce: str
    difficulty: int

    def to_json(self) -> dict[str, str | int]:
        return {"nonce": self.nonce, "difficulty": self.difficulty, "algorithm": "sha256"}


SOLVED_WINDOW_SECONDS = 24 * 3600


class PowChallenges:
    def __init__(
        self,
        redis: Redis,
        difficulty: int,
        ttl_seconds: int,
        escalation_bits: int = 0,
        max_bits: int = 28,
    ) -> None:
        self._redis = redis
        self._difficulty = difficulty
        self._ttl = ttl_seconds
        self._escalation = escalation_bits
        self._max = max(max_bits, difficulty)

    @staticmethod
    def _key(user_id: UUID, nonce: str) -> str:
        return f"pow:{user_id}:n:{nonce}"

    async def _count(self, key: str) -> int:
        raw = await self._redis.get(key)
        return int(raw.decode() if isinstance(raw, bytes) else raw) if raw is not None else 0

    async def open(self, user_id: UUID) -> int:
        """Puzzles issued to the account and not solved (within the puzzle TTL)."""
        return max(await self._count(f"pow:{user_id}:open"), 0)

    async def issue(self, user_id: UUID) -> Puzzle:
        solved = await self._count(f"pow:{user_id}:solved")
        difficulty = min(self._difficulty + self._escalation * solved, self._max)
        nonce = secrets.token_hex(16)
        pipe = self._redis.pipeline(transaction=True)
        pipe.set(self._key(user_id, nonce), str(difficulty), ex=self._ttl)
        pipe.incr(f"pow:{user_id}:open")
        pipe.expire(f"pow:{user_id}:open", self._ttl)
        await pipe.execute()
        return Puzzle(nonce, difficulty)

    async def redeem(self, user_id: UUID, solution: str | None) -> bool:
        """True once per issued nonce, for its account, with a valid counter."""
        if not solution or ":" not in solution or len(solution) > 128:
            return False
        nonce, counter = solution.split(":", 1)
        stored = await self._redis.getdel(self._key(user_id, nonce))
        if stored is None:
            return False
        difficulty = int(stored.decode() if isinstance(stored, bytes) else stored)
        if not is_solution(nonce, counter, difficulty):
            return False
        pipe = self._redis.pipeline(transaction=True)
        pipe.decr(f"pow:{user_id}:open")
        pipe.incr(f"pow:{user_id}:solved")
        pipe.expire(f"pow:{user_id}:solved", SOLVED_WINDOW_SECONDS, nx=True)
        await pipe.execute()
        return True
