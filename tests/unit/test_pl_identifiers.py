"""Chunk 3.1: Polish identifier validators (property-based + known edge cases)."""

from __future__ import annotations

import datetime as dt
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from spoldzielnia_ai.privacy.pl_identifiers import (
    find_checksum_identifiers,
    is_valid_iban,
    is_valid_krs,
    is_valid_nip,
    is_valid_nrb,
    is_valid_parcel_id,
    is_valid_pesel,
    is_valid_regon,
    pesel_birth_date,
)
from tests.fixtures.pl_ids import (
    make_iban_pl,
    make_nip,
    make_pesel,
    make_regon9,
    make_regon14,
    mutate_digit,
    spaced,
)

dates = st.dates(min_value=dt.date(1800, 1, 1), max_value=dt.date(2299, 12, 31))
seeds = st.integers(min_value=0, max_value=2**32)
deltas = st.integers(min_value=1, max_value=9)


# --- PESEL ---------------------------------------------------------------------------------


@given(dates, st.integers(0, 999), st.booleans())
def test_generated_pesel_is_valid_and_encodes_birth_date(
    birth: dt.date, serial: int, female: bool
) -> None:
    pesel = make_pesel(birth, serial, female)
    assert is_valid_pesel(pesel)
    assert pesel_birth_date(pesel) == birth
    assert int(pesel[9]) % 2 == (0 if female else 1)


@given(dates, st.integers(0, 999), st.integers(0, 10), deltas)
def test_single_digit_change_breaks_pesel(
    birth: dt.date, serial: int, position: int, delta: int
) -> None:
    assert not is_valid_pesel(mutate_digit(make_pesel(birth, serial), position, delta))


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (make_pesel(dt.date(2004, 2, 29)), True),  # 2000+ birth: month 22, leap day
        (make_pesel(dt.date(1899, 12, 31)), True),  # 1800s: month +80
        ("44051401359", True),  # the well-known documentation example
        ("44051401358", False),  # wrong check digit
        ("44053201356", False),  # 32 May: no such date
        ("4405140135", False),  # 10 digits
        ("4405140135a", False),
        ("440514 01359", True),  # separators are normalised
    ],
)
def test_pesel_edge_cases(value: str, valid: bool) -> None:
    assert is_valid_pesel(value) is valid


# --- NIP -----------------------------------------------------------------------------------


@given(seeds)
def test_generated_nip_is_valid_in_every_notation(seed: int) -> None:
    nip = make_nip(random.Random(seed))
    for notation in (
        nip,
        f"PL{nip}",
        f"PL {nip}",
        f"{nip[:3]}-{nip[3:6]}-{nip[6:8]}-{nip[8:]}",
        f"{nip[:3]}-{nip[3:5]}-{nip[5:7]}-{nip[7:]}",
    ):
        assert is_valid_nip(notation), notation


@given(seeds, st.integers(0, 9), deltas)
def test_single_digit_change_breaks_nip(seed: int, position: int, delta: int) -> None:
    """NIP refuses residue 10 outright, so (unlike REGON) every single-digit error is caught."""
    nip = make_nip(random.Random(seed))
    assert not is_valid_nip(mutate_digit(nip, position, delta))


@pytest.mark.parametrize("value", ["0000000000", "1111111111", "123456789", "12345678901"])
def test_nip_rejects_trivial_and_wrong_length(value: str) -> None:
    assert not is_valid_nip(value)


# --- REGON / KRS ---------------------------------------------------------------------------


@given(seeds)
def test_generated_regons_are_valid(seed: int) -> None:
    rng = random.Random(seed)
    assert is_valid_regon(make_regon9(rng))
    assert is_valid_regon(make_regon14(rng))


@given(seeds, st.integers(0, 7), deltas)
def test_single_digit_change_breaks_regon9(seed: int, position: int, delta: int) -> None:
    """REGON maps residue 10 to check digit 0, so residues 0 and 10 collide. That is the one
    blind spot of the official algorithm. Every other single-digit error is caught."""
    regon = make_regon9(random.Random(seed))
    mutated = mutate_digit(regon, position, delta)
    if is_valid_regon(mutated):
        residues = {_regon9_residue(regon), _regon9_residue(mutated)}
        assert residues == {0, 10}


def _regon9_residue(regon: str) -> int:
    from spoldzielnia_ai.privacy.pl_identifiers import REGON9_WEIGHTS

    return sum(int(d) * w for d, w in zip(regon[:8], REGON9_WEIGHTS, strict=True)) % 11


def test_regon14_requires_valid_regon9_prefix() -> None:
    regon = make_regon14()
    broken_prefix = mutate_digit(regon, 0)
    assert not is_valid_regon(broken_prefix)


def test_krs_is_format_only() -> None:
    assert is_valid_krs("0000123456")
    assert not is_valid_krs("0000000000")
    assert not is_valid_krs("123456789")


# --- IBAN / NRB ----------------------------------------------------------------------------


@given(seeds)
def test_generated_iban_and_nrb_are_valid(seed: int) -> None:
    iban = make_iban_pl(random.Random(seed))
    assert is_valid_iban(iban)
    assert is_valid_iban(spaced(iban))
    assert is_valid_nrb(iban[2:])
    assert is_valid_nrb(spaced(iban[2:]))


@given(seeds, st.integers(2, 27), deltas)
@settings(max_examples=200)
def test_single_digit_change_breaks_iban(seed: int, position: int, delta: int) -> None:
    iban = make_iban_pl(random.Random(seed))
    assert not is_valid_iban(mutate_digit(iban, position, delta))


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("DE89370400440532013000", True),  # ISO example
        ("GB82WEST12345698765432", True),  # ISO example
        ("PL61109010140000071219812874", True),  # well-known PL example
        ("PL61109010140000071219812875", False),
        ("PL6110901014000007121981287", False),  # 27 chars
    ],
)
def test_iban_examples(value: str, valid: bool) -> None:
    assert is_valid_iban(value) is valid


# --- Parcels -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("146501_1.0001.123/4", True),  # Mazowieckie (14), urban gmina
        ("020101_2.0005.77", True),
        ("150101_1.0001.1", False),  # 15: odd voivodeship code
        ("340101_1.0001.1", False),  # 34: no such voivodeship
        ("146501_0.0001.1", False),  # unit type 0
        ("146501-1.0001.1", False),
    ],
)
def test_parcel_ids(value: str, valid: bool) -> None:
    assert is_valid_parcel_id(value) is valid


# --- Digit runs in free text ---------------------------------------------------------------


def test_identifiers_are_found_in_reformatted_text() -> None:
    pesel = make_pesel(dt.date(1975, 6, 1))
    nip = make_nip()
    iban = make_iban_pl()
    text = (
        f"Kowalski (PESEL {pesel[:6]} {pesel[6:]}), firma NIP {nip[:3]}-{nip[3:6]}-{nip[6:8]}-"
        f"{nip[8:]}, konto {spaced(iban)}. Telefon 600 700 800, rok 2024."
    )
    kinds = sorted(kind for kind, _, _ in find_checksum_identifiers(text))
    assert kinds == ["IBAN", "NIP", "PESEL"]


@given(st.integers(min_value=10**10, max_value=10**11 - 1))
@settings(max_examples=300)
def test_random_11_digit_numbers_are_rarely_pesel(number: int) -> None:
    """Precision: the date + checksum filter rejects most random numbers (no assertion on
    one example, only that validation never crashes and agrees with the date rule)."""
    value = str(number)
    if is_valid_pesel(value):
        assert pesel_birth_date(value) is not None
