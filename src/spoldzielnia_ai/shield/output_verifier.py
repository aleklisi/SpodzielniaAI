"""``OutputVerifier``: no verbatim extraction through the answer (5.5, SECURITY.md layer 3).

The model is told not to quote (``rag.synthesis``), but the prompt is not the control: this
check runs on every draft before it reaches the client. Text is normalised (lower case,
diacritics folded, punctuation, Markdown and citation markers stripped) and compared with
each source block. The answer is refused if any of these holds:

- **5-gram overlap** with one source: ``|5grams(answer) ∩ 5grams(source)| / |5grams(source)|``
  above ``verbatim_overlap_max`` (one block copied out);
- **ROUGE-L** F1 (longest common subsequence) against all sources together above
  ``verbatim_rouge_l_max`` (the sources dumped, possibly reordered);
- a **verbatim span** longer than ``verbatim_span_max_words`` words;
- a **JSON dump**: a JSON array/object in the answer whose strings repeat source text.

``extraction_intent`` flags questions asking for exactly that ("zacytuj w całości", "słowo w
słowo", "verbatim" …): the guard adds risk and asks for the strict prompt, and the verifier
still decides.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.shield.translation import TranslationResult

N = 5
JSON_SOURCE_OVERLAP = 0.5

_MARKER = re.compile(r"\[(?:S?\d{1,3})\]")
_WORD = re.compile(r"\w+")

# Normalised (folded) phrases. Each entry: (name, pattern).
EXTRACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("cytuj_calosc", r"\bzacytuj\w*\b.*\b(w calosci|calosc\w*|caly|cala|cale)\b"),
        ("slowo_w_slowo", r"\bslowo w slowo\b"),
        ("doslownie", r"\bdoslown\w*\b"),
        ("cala_tresc", r"\b(cal\w*|peln\w*) tresc\w*\b"),
        ("wypisz_wszystko", r"\b(wypisz|przepisz|skopiuj|wklej)\b.*\b(wszystk\w*|cal\w*)\b"),
        ("surowe_dane", r"\bsurow\w* (dane|tekst\w*|tresc\w*|fragment\w*|blok\w*)\b"),
        ("json_z_trescia", r"\bjson\w*\b.*\b(tresc\w*|tekst\w*|dane|blok\w*|artykul\w*)\b"),
        ("verbatim", r"\bverbatim\b"),
        ("word_for_word", r"\bword for word\b"),
        ("dump", r"\bdump\w*\b"),
        ("raw_data", r"\braw (data|text|chunks?|content)\b"),
        ("full_text", r"\b(full|entire|whole) (text|article|content|document)\b"),
        # English answers (14.5): copying through a translation, or asking to quote it all.
        (
            "przetlumacz_calosc",
            r"\bprzetlumacz\w*\b.*\b(calos\w*|cal\w*|wszystk\w*|zdanie po zdaniu)\b",
        ),
        (
            "translate_in_full",
            r"\btranslat\w*\b.*\b(full|entire|whole|all|everything|sentence by sentence)\b",
        ),
        ("quote_in_full", r"\bquote\w*\b.*\b(full|entire|whole|all|everything)\b"),
        (
            "copy_everything",
            r"\b(copy|paste|reproduce|print|list)\b.*"
            r"\b(everything|all (of )?(the )?(text|articles?|content|blocks?))\b",
        ),
    )
)


def fold(text: str) -> str:
    """Lower case, Polish diacritics folded (``ł`` included)."""
    lowered = text.lower().replace("ł", "l")
    return "".join(
        c for c in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(c)
    )


def words(text: str) -> list[str]:
    return _WORD.findall(fold(_MARKER.sub(" ", text)))


def extraction_intent(question: str) -> list[str]:
    """Names of the extraction patterns the question matches (empty: none)."""
    folded = " ".join(words(question))
    return [name for name, pattern in EXTRACTION_PATTERNS if pattern.search(folded)]


def ngrams(tokens: Sequence[str], n: int = N) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def lcs_length(a: Sequence[str], b: Sequence[str]) -> int:
    """Length of the longest common subsequence, bit-parallel (Hyyrö): O(|b| · |a|/64)."""
    if not a or not b:
        return 0
    masks: dict[str, int] = {}
    for i, token in enumerate(a):
        masks[token] = masks.get(token, 0) | (1 << i)
    full = (1 << len(a)) - 1
    v = full
    for token in b:
        u = v & masks.get(token, 0)
        v = ((v + u) | (v - u)) & full
    return len(a) - v.bit_count()


def rouge_l(answer: Sequence[str], reference: Sequence[str]) -> float:
    lcs = lcs_length(answer, reference)
    if lcs == 0:
        return 0.0
    precision, recall = lcs / len(answer), lcs / len(reference)
    return 2 * precision * recall / (precision + recall)


def longest_common_span(a: Sequence[str], b: Sequence[str]) -> int:
    """Longest run of consecutive words shared by ``a`` and ``b`` (binary search on length)."""
    if not a or not b:
        return 0
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ngrams(a, mid) & ngrams(b, mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def _json_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in cast("dict[str, Any]", value).values():
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in cast("list[Any]", value):
            yield from _json_strings(item)


def json_documents(text: str) -> list[Any]:
    """Every JSON object or array embedded in ``text`` (outermost ones)."""
    decoder = json.JSONDecoder()
    found: list[Any] = []
    i = 0
    while i < len(text):
        if text[i] in "{[":
            try:
                value, end = decoder.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            if isinstance(value, dict | list) and value:
                found.append(value)
            i = end
            continue
        i += 1
    return found


@dataclass(frozen=True, slots=True)
class Verification:
    blocked: bool
    reason: str | None
    max_overlap: float
    rouge_l: float
    longest_span: int
    json_dump: bool
    translation: TranslationResult | None = None  # answers in another language (14.5)

    def with_translation(self, result: TranslationResult) -> Verification:
        blocked = self.blocked or result.blocked
        reason = self.reason or ("translation" if result.blocked else None)
        return replace(self, blocked=blocked, reason=reason, translation=result)

    def to_json(self) -> dict[str, float | int | str | bool | None]:
        out: dict[str, float | int | str | bool | None] = {
            "reason": self.reason,
            "max_overlap": round(self.max_overlap, 4),
            "rouge_l": round(self.rouge_l, 4),
            "longest_span": self.longest_span,
            "json_dump": self.json_dump,
        }
        if self.translation is not None:
            out.update(self.translation.to_json())
        return out


class OutputVerifier:
    def __init__(self, settings: ShieldSettings) -> None:
        self._settings = settings

    def verify(self, answer: str, sources: Sequence[str]) -> Verification:
        s = self._settings
        answer_words = words(answer)
        source_words = [words(text) for text in sources]
        answer_grams = ngrams(answer_words)
        max_overlap = 0.0
        for tokens in source_words:
            grams = ngrams(tokens)
            if grams:
                max_overlap = max(max_overlap, len(answer_grams & grams) / len(grams))
        reference = [t for tokens in source_words for t in tokens]
        rouge = rouge_l(answer_words, reference)
        span = max((longest_common_span(answer_words, t) for t in source_words), default=0)
        dump = self._json_dump(answer, source_words)
        reason = None
        if max_overlap > s.verbatim_overlap_max:
            reason = "ngram_overlap"
        elif rouge > s.verbatim_rouge_l_max:
            reason = "rouge_l"
        elif span > s.verbatim_span_max_words:
            reason = "verbatim_span"
        elif dump:
            reason = "json_dump"
        return Verification(reason is not None, reason, max_overlap, rouge, span, dump)

    @staticmethod
    def _json_dump(answer: str, source_words: Sequence[Sequence[str]]) -> bool:
        source_grams: set[tuple[str, ...]] = set()
        for tokens in source_words:
            source_grams |= ngrams(tokens)
        for document in json_documents(answer):
            grams = ngrams(words(" ".join(_json_strings(document))))
            if grams and len(grams & source_grams) / len(grams) >= JSON_SOURCE_OVERLAP:
                return True
        return False
