"""Chunk 14.5: an English answer that translates a Polish source is refused; a summary is not.

The embedder is a stand-in for a multilingual model: a sentence and its translation share a
"meaning" (one axis of the vector), anything else gets its own axis.
"""

from __future__ import annotations

import pytest

from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.shield.translation import (
    MAX_SENTENCES,
    TranslationCheck,
    TranslationUncheckable,
    sentences,
)

SOURCE = (
    "Zimowe leczenie warrozy kwasem szczawiowym wykonuje się w okresie bezczerwiowym. "
    "Zwykle robi się to w grudniu, gdy rodzina tworzy zwarty kłąb. "
    "Roztwór przygotowuje się w syropie cukrowym i podaje strzykawką na uliczkę. "
    "Skuteczność ocenia się po dwóch tygodniach na podstawie osypu roztoczy."
)
TRANSLATION = (
    "Winter varroa treatment with oxalic acid is done in the broodless period [S1]. "
    "This is usually done in December, when the colony forms a tight cluster [S1]. "
    "The solution is prepared in sugar syrup and given with a syringe per seam [S1]. "
    "Effectiveness is assessed after two weeks from the mite drop [S1]."
)
SUMMARY = (
    "Treat varroa with oxalic acid in winter, once there is no brood, and check the mite "
    "drop two weeks later [S1]."
)
MEANINGS = [
    (
        "Zimowe leczenie warrozy kwasem szczawiowym wykonuje się w okresie bezczerwiowym.",
        "Winter varroa treatment with oxalic acid is done in the broodless period.",
    ),
    (
        "Zwykle robi się to w grudniu, gdy rodzina tworzy zwarty kłąb.",
        "This is usually done in December, when the colony forms a tight cluster.",
    ),
    (
        "Roztwór przygotowuje się w syropie cukrowym i podaje strzykawką na uliczkę.",
        "The solution is prepared in sugar syrup and given with a syringe per seam.",
    ),
    (
        "Skuteczność ocenia się po dwóch tygodniach na podstawie osypu roztoczy.",
        "Effectiveness is assessed after two weeks from the mite drop.",
    ),
]
DIM = 64


class MeaningEmbedder:
    def __init__(self) -> None:
        self.axis = {text: i for i, pair in enumerate(MEANINGS) for text in pair}
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        out: list[list[float]] = []
        for text in texts:
            axis = self.axis.get(text, len(MEANINGS) + hash(text) % (DIM - len(MEANINGS)))
            vector = [0.05] * DIM
            vector[axis] = 1.0
            out.append(vector)
        return out


class BrokenEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model unavailable")


SETTINGS = ShieldSettings()


def _check(embedder: object | None) -> TranslationCheck:
    return TranslationCheck(embedder, SETTINGS)  # type: ignore[arg-type]


def test_sentences_drop_markup_markers_and_fragments() -> None:
    text = (
        "## Leczenie\n- **Kwas** szczawiowy podaje się zimą [S1].\nKrótko. Tak.\n"
        "To jest pełne zdanie."
    )
    assert sentences(text) == ["Kwas szczawiowy podaje się zimą.", "To jest pełne zdanie."]


async def test_a_translation_of_a_source_is_refused() -> None:
    result = await _check(MeaningEmbedder()).check(TRANSLATION, [SOURCE])
    assert result.blocked
    assert result.max_share == pytest.approx(1.0)
    assert result.matched_words >= SETTINGS.verbatim_xlang_min_words


async def test_a_partial_translation_of_most_of_a_source_is_refused() -> None:
    three = " ".join(english for _, english in MEANINGS[:3])
    result = await _check(MeaningEmbedder()).check(three, [SOURCE])
    assert result.blocked
    assert result.max_share > SETTINGS.verbatim_xlang_share


async def test_a_summary_passes() -> None:
    result = await _check(MeaningEmbedder()).check(SUMMARY, [SOURCE])
    assert not result.blocked
    assert result.max_share == 0.0


async def test_one_translated_sentence_is_below_the_limits() -> None:
    one = f"{MEANINGS[0][1]} Everything else is my own advice on the matter at hand."
    result = await _check(MeaningEmbedder()).check(one, [SOURCE])
    assert not result.blocked
    assert 0 < result.max_share < SETTINGS.verbatim_xlang_share


async def test_the_translated_source_is_found_among_several() -> None:
    other = "Pszczoły potrzebują wody przez cały rok, także zimą w cieplejsze dni."
    result = await _check(MeaningEmbedder()).check(TRANSLATION, [other, SOURCE])
    assert result.blocked


async def test_without_an_embedder_the_check_cannot_run() -> None:
    with pytest.raises(TranslationUncheckable):
        await _check(None).check(TRANSLATION, [SOURCE])


async def test_an_embedding_outage_is_uncheckable_not_a_pass() -> None:
    with pytest.raises(TranslationUncheckable):
        await _check(BrokenEmbedder()).check(TRANSLATION, [SOURCE])


async def test_too_much_text_is_uncheckable_never_truncated() -> None:
    huge = " ".join(f"Zdanie numer {i} jest tutaj całkiem zwyczajne." for i in range(MAX_SENTENCES))
    with pytest.raises(TranslationUncheckable):
        await _check(MeaningEmbedder()).check(TRANSLATION, [huge])


async def test_nothing_to_compare_needs_no_model() -> None:
    embedder = MeaningEmbedder()
    result = await _check(embedder).check("Tak [S1].", [SOURCE])
    assert not result.blocked
    assert embedder.calls == []
