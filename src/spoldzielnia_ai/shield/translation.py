"""``TranslationCheck``: no verbatim extraction through a translated answer (14.5).

The ``OutputVerifier`` compares words, so a translation of a source block slips past it: an
English answer shares almost no 5-grams with Polish sources. For an answer written in another
language than the sources, this check compares **meaning** instead, with the configured
embedding model (multilingual): every source sentence is matched with its most similar answer
sentence. A source counts as translated when the sentences matched above
``verbatim_xlang_similarity`` hold at least ``verbatim_xlang_share`` of its words and at least
``verbatim_xlang_min_words`` words. A summary condenses several source sentences into one, which
is far from each of them. A translation keeps one answer sentence per source sentence.

The thresholds start strict and are meant to be calibrated on real pairs (14.5 risks). If the
check cannot run (no embedding model, an outage), ``TranslationUncheckable`` is raised and the
caller answers in Polish, checked by the word-level verifier: an English answer is never
returned unchecked.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from spoldzielnia_ai.shield.output_verifier import words

if TYPE_CHECKING:
    from collections.abc import Sequence

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.vector.embedding import Embedder

MIN_SENTENCE_WORDS = 4
MAX_SENTENCES = 400  # one embedding batch at most a few times the default batch size

_SPLIT = re.compile(r"(?<=[.!?…;:])\s+|\n+")
_MARKUP = re.compile(r"\[(?:S?\d{1,3})\]|[#*_>`|]+")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-+]|\d{1,3}[.)])[ \t]+", re.M)
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?…])")


class TranslationUncheckable(Exception):
    """The cross-language check could not run."""


@dataclass(frozen=True, slots=True)
class TranslationResult:
    blocked: bool
    max_share: float  # the largest share of one source's words matched by answer sentences
    matched_words: int  # words of that source matched

    def to_json(self) -> dict[str, float | int]:
        return {"xlang_share": round(self.max_share, 4), "xlang_words": self.matched_words}


def sentences(text: str) -> list[str]:
    """Sentences of at least ``MIN_SENTENCE_WORDS`` words, markup and citation markers removed."""
    out: list[str] = []
    plain = _MARKUP.sub(" ", _LIST_ITEM.sub("", text))
    for piece in _SPLIT.split(plain):
        piece = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", " ".join(piece.split()))
        if len(words(piece)) >= MIN_SENTENCE_WORDS:
            out.append(piece)
    return out


def _unit(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class TranslationCheck:
    def __init__(self, embedder: Embedder | None, settings: ShieldSettings) -> None:
        self._embedder = embedder
        self._settings = settings

    async def check(self, answer: str, sources: Sequence[str]) -> TranslationResult:
        s = self._settings
        answer_sentences = sentences(answer)
        source_sentences = [sentences(text) for text in sources]
        if not answer_sentences or not any(source_sentences):
            return TranslationResult(False, 0.0, 0)
        texts = answer_sentences + [x for group in source_sentences for x in group]
        if len(texts) > MAX_SENTENCES:
            # Never truncate silently: an unchecked tail could carry the copy.
            raise TranslationUncheckable("too many sentences")
        if self._embedder is None:
            raise TranslationUncheckable("no embedder")
        try:
            vectors = [_unit(v) for v in await self._embedder.embed(texts)]
        except Exception as exc:
            raise TranslationUncheckable(type(exc).__name__) from exc
        if len(vectors) != len(texts):
            raise TranslationUncheckable("embedding count mismatch")
        answer_vectors, rest = vectors[: len(answer_sentences)], vectors[len(answer_sentences) :]
        best = TranslationResult(False, 0.0, 0)
        offset = 0
        for group in source_sentences:
            total = sum(len(words(x)) for x in group)
            matched = 0
            for i, sentence in enumerate(group):
                vector = rest[offset + i]
                if max(_cosine(vector, a) for a in answer_vectors) >= s.verbatim_xlang_similarity:
                    matched += len(words(sentence))
            offset += len(group)
            share = matched / total if total else 0.0
            blocked = share >= s.verbatim_xlang_share and matched >= s.verbatim_xlang_min_words
            if blocked or share > best.max_share:
                best = TranslationResult(blocked, share, matched)
            if blocked:
                break
        return best
