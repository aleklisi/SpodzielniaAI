"""Chunk 5.5: the deterministic lexical ``Watermarker`` and attribution by z-score."""

from __future__ import annotations

import random
import re
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.shield.watermark import (
    PAIRS,
    PAIRS_EN,
    SLOT_OF,
    TABLES,
    Watermarker,
    attributed,
)

KEY = b"watermark-test-key-0123456789abcdef"

POOL = [
    "Ponadto warto stosować odpowiednie preparaty zgodnie z zaleceniami [S1].",
    "Zwykle zabieg wykonuje się wówczas, gdy w ulu jest mało czerwiu [S2].",
    "Jednak w przypadku silnej inwazji należy działać natychmiast [S1].",
    "Główne objawy to zniekształcone skrzydła oraz duże osypy [S3].",
    "Kontrola osypu pozwala na bieżąco ocenić skuteczne działanie preparatu [S2].",
    "Dlatego przede wszystkim warto zapewnić rodzinie odpowiednią ilość pokarmu [S1].",
    "Metoda ta jest prosta, ale wymaga odmierzenia 50 ml roztworu na rodzinę [S2].",
    "Następnie należy sprawdzić wyniki po około 14 dniach [S1].",
    "Ryzyko jest wówczas znacznie mniejsze, zatem efekt widać szybko [S3].",
    "Również ważne jest, aby nie stosować preparatów podczas pożytku [S2].",
    "Na przykład kwas szczawiowy stosuje się głównie zimą [S1].",
    "Mimo to problem może wrócić, ponieważ roztocza rozwijają się w czerwiu [S2].",
    "Szczególnie w dużych pasiekach rodzaj zabiegu dobiera się do etapu sezonu [S3].",
    "Konieczne jest co najmniej dwukrotne sprawdzenie stanu rodziny [S1].",
    "Często wystarczy niewielka zmiana, jednakże trzeba ją wprowadzić regularnie [S2].",
    "Obecnie zaleca się, żeby wyniki zapisywać dokładnie w dzienniku pasieki [S3].",
]


def _answers(count: int, seed: int = 3) -> list[str]:
    rng = random.Random(seed)
    answers: list[str] = []
    for _ in range(count):
        body = " ".join(rng.sample(POOL, 5))
        answers.append(f"{body}\n\n- Pierwszy krok [S1]\n- Drugi krok [S2]\n\n1. Ocena [S3]")
    return answers


def test_detector_singles_out_the_account_over_100_candidates() -> None:
    """Ten answers for A, leaked together: A ranks first with z > 4, nobody else crosses."""
    marker = Watermarker(KEY)
    alice = uuid4()
    leaked = "\n\n".join(marker.apply(answer, alice) for answer in _answers(10))
    candidates = [uuid4() for _ in range(100)] + [alice]

    ranking = marker.detect(leaked, candidates)

    assert ranking[0].user_id == alice
    assert ranking[0].z > 4
    assert ranking[0].agree == ranking[0].slots  # every slot follows Alice's key
    assert attributed(ranking, 4.0) == [ranking[0]]


def test_unwatermarked_text_attributes_to_nobody() -> None:
    marker = Watermarker(KEY)
    text = "\n\n".join(_answers(10))
    ranking = marker.detect(text, [uuid4() for _ in range(100)])
    assert ranking[0].slots >= 16
    assert attributed(ranking, 4.0) == []


def test_meaning_critical_tokens_are_untouched() -> None:
    marker = Watermarker(KEY)
    text = (
        "Należy podać 50 ml [S1] (zob. https://example.org/należy-ponadto) oraz `należy` "
        "w kodzie:\n```\nponadto = 3.5\n```\nPonadto [2] wynik: 12,5%."
    )
    out = marker.apply(text, uuid4())

    def protected(s: str) -> list[str]:
        return re.findall(r"\d+(?:[.,]\d+)?|\[S?\d+\]|https?://\S+|`[^`]*`|```.*?```", s, re.S)

    assert protected(out) == protected(text)


def test_case_is_kept_and_output_is_stable() -> None:
    marker = Watermarker(KEY)
    user = uuid4()
    out = marker.apply("Ponadto PONADTO ponadto.", user)
    variant = PAIRS[SLOT_OF["ponadto"][0]][marker.bit(user, "ponadto")]
    assert out == f"{variant.capitalize()} {variant.upper()} {variant}."
    assert marker.apply(out, user) == out  # idempotent
    # The same account gets the same choice in every answer.
    assert variant in marker.apply("To ważne, ponadto proste.", user)


def test_structural_slots() -> None:
    marker = Watermarker(KEY)
    user = UUID(int=7)
    out = marker.apply("Kroki:\n- jeden\n* dwa\n1. trzy\n2) cztery", user)
    bullet = "-*"[marker.bit(user, "bullet")]
    numbering = ".)"[marker.bit(user, "numbering")]
    assert out == f"Kroki:\n{bullet} jeden\n{bullet} dwa\n1{numbering} trzy\n2{numbering} cztery"


def test_dictionary_is_unambiguous() -> None:
    forms = [form for pair in PAIRS for form in pair]
    assert len(forms) == len(set(forms))
    single = {f for f in forms if " " not in f}
    assert [f for f in forms if " " in f and set(f.split()) & single] == []
    assert all(not re.search(r"\d", f) for f in forms)
    assert len(PAIRS) >= 150


def test_key_comes_from_settings() -> None:
    assert Watermarker.from_settings(ShieldSettings()) is None
    marker = Watermarker.from_settings(ShieldSettings(watermark_key=SecretStr("k")))
    assert marker is not None
    with pytest.raises(ValueError, match="empty"):
        Watermarker(b"")


# --- English answers (14.5) -----------------------------------------------------------------

POOL_EN = [
    "Moreover, it is important to treat colonies regularly with a suitable method [S1].",
    "Usually the treatment begins when the colony is almost free of brood [S2].",
    "In case of a strong infestation, act immediately and make sure the dose is sufficient [S1].",
    "The main symptoms are visible damage to wings and a large mite drop [S3].",
    "For example, oxalic acid is mainly recommended in winter, especially in large apiaries [S2].",
    "Initially the colony is assessed carefully; eventually the results are typical [S1].",
    "Sometimes various techniques are combined, which allows a rapid and complete effect [S3].",
    "Therefore the recommendations are simple: follow them gradually and partially at first [S2].",
]


def _answers_en(count: int, seed: int = 5) -> list[str]:
    rng = random.Random(seed)
    return [
        " ".join(rng.sample(POOL_EN, 5)) + "\n\n- First step [S1]\n- Second step [S2]"
        for _ in range(count)
    ]


def test_english_answers_use_the_english_table_only() -> None:
    marker = Watermarker(KEY)
    user = UUID(int=11)
    text = "Ponadto it is important to check hives regularly, for example in spring [S1]."
    out = marker.apply(text, user, "en")
    assert out.startswith("Ponadto")  # a Polish form in an English answer is left alone
    assert out.endswith("[S1].")
    regularly = ("regularly", "routinely")[marker.bit(user, "en:regularly")]
    assert regularly in out


def test_english_detector_singles_out_the_account() -> None:
    marker = Watermarker(KEY)
    alice = uuid4()
    leaked = "\n\n".join(marker.apply(answer, alice, "en") for answer in _answers_en(10))
    ranking = marker.detect(leaked, [uuid4() for _ in range(100)] + [alice])
    assert ranking[0].user_id == alice
    assert ranking[0].z > 4
    assert attributed(ranking, 4.0) == [ranking[0]]


def test_polish_slots_keep_their_names() -> None:
    """Answers watermarked before 14.5 are still attributed: Polish slots are unprefixed."""
    assert TABLES["pl"].slot(SLOT_OF["ponadto"][0]) == "ponadto"
    assert TABLES["en"].slot(TABLES["en"].slot_of["moreover"][0]) == "en:moreover"


def test_english_dictionary_is_unambiguous_and_apart_from_polish() -> None:
    forms = [form for pair in PAIRS_EN for form in pair]
    assert len(forms) == len(set(forms))
    for form in forms:
        others = [f for f in forms if f != form]
        assert not [o for o in others if re.search(rf"(?<!\w){re.escape(o)}(?!\w)", form)]
    assert not set(forms) & set(SLOT_OF)
    assert all(not re.search(r"\d", f) for f in forms)
    assert len(PAIRS_EN) >= 50
