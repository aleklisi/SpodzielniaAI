"""Chunk 9.1: label names are matched without case or Polish diacritics."""

from __future__ import annotations

import pytest

from spoldzielnia_ai.atlas.text import clean_name, fold, search_key, slugify


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("Matka Pszczoła", "matka pszczola"),
        ("  ŻÓŁTY   Łan ", "zolty lan"),
        ("Źdźbło, ćma, ślimak, ąę", "zdzblo, cma, slimak, ae"),
        ("STRAßE", "strasse"),
    ],
)
def test_fold(raw: str, folded: str) -> None:
    assert fold(raw) == folded


def test_slug_and_clean_name() -> None:
    assert slugify("Warroza (Varroa destructor)") == "warroza-varroa-destructor"
    assert slugify("Truteń") == "truten"
    assert slugify("!!!") == ""
    assert clean_name("  Matka \t pszczela ") == "Matka pszczela"


def test_search_key_deduplicates_folded_values() -> None:
    key = search_key("Matka pszczela", ["matka", "Matka", "Królowa", " "])
    assert key.split("\n") == ["matka pszczela", "matka", "krolowa"]
