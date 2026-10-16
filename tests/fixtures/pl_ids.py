"""Algorithmically generated Polish identifiers (no real person's data in the repository)."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from spoldzielnia_ai.privacy.pl_identifiers import (
    NIP_WEIGHTS,
    PESEL_WEIGHTS,
    REGON9_WEIGHTS,
    REGON14_WEIGHTS,
)

if TYPE_CHECKING:
    import datetime as dt

_MONTH_OFFSET = {1800: 80, 1900: 0, 2000: 20, 2100: 40, 2200: 60}


def _weighted(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False))


def make_pesel(birth: dt.date, serial: int = 123, female: bool = False) -> str:
    century = birth.year // 100 * 100
    month = birth.month + _MONTH_OFFSET[century]
    sex_digit = (serial % 5) * 2 + (0 if female else 1)
    body = f"{birth.year % 100:02d}{month:02d}{birth.day:02d}{serial % 1000:03d}{sex_digit}"
    check = (10 - _weighted(body, PESEL_WEIGHTS) % 10) % 10
    return body + str(check)


def make_nip(rng: random.Random | None = None) -> str:
    rng = rng or random.Random(7)
    while True:
        body = "".join(str(rng.randint(0, 9)) for _ in range(9))
        check = _weighted(body, NIP_WEIGHTS) % 11
        if check != 10 and len(set(body)) > 1:
            return body + str(check)


def make_regon9(rng: random.Random | None = None) -> str:
    rng = rng or random.Random(9)
    body = "".join(str(rng.randint(0, 9)) for _ in range(8))
    return body + str(_weighted(body, REGON9_WEIGHTS) % 11 % 10)


def make_regon14(rng: random.Random | None = None) -> str:
    rng = rng or random.Random(14)
    body = make_regon9(rng) + "".join(str(rng.randint(0, 9)) for _ in range(4))
    return body + str(_weighted(body, REGON14_WEIGHTS) % 11 % 10)


def make_iban_pl(rng: random.Random | None = None) -> str:
    rng = rng or random.Random(26)
    bban = "".join(str(rng.randint(0, 9)) for _ in range(24))
    numeric = "".join(str(int(ch, 36)) for ch in bban + "PL00")
    check = 98 - int(numeric) % 97
    return f"PL{check:02d}{bban}"


def spaced(iban: str) -> str:
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def mutate_digit(value: str, position: int, delta: int = 1) -> str:
    """The same identifier with one digit changed (always breaks a mod-10/11/97 checksum
    for a single substitution, except where weight 0 makes the position irrelevant)."""
    digits = list(value)
    original = int(digits[position])
    digits[position] = str((original + delta) % 10)
    return "".join(digits)
