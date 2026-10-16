"""PII scrubbing (chunk 3.2): typed and engine-agnostic.

``PiiScrubber`` takes findings from an ``Analyzer`` (Presidio in production, fakes in tests),
resolves overlaps and rewrites the text:

- identifiers become type tags: ``<PESEL>``, ``<NIP>``, ``<IBAN>`` …
- people and places become **stable pseudonyms** within one document (``<OSOBA_1>``,
  ``<MIEJSCE_2>``), so the LLM keeps track of who is who without seeing names. Polish
  inflects names ("Jan Kowalski", "Jana Kowalskiego"), so mentions are keyed by the first three
  letters of each token, and a bare surname joins a known full name.

``ScrubResult.findings`` hold the entity type, offsets **in the original text** and score.
The original values are never stored.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

# Entity → replacement tag. Entities missing here are kept (e.g. ORGANIZATION by default).
TAGS: dict[str, str] = {
    "PL_PESEL": "PESEL",
    "PL_NIP": "NIP",
    "PL_REGON": "REGON",
    "PL_KRS": "KRS",
    "IBAN_CODE": "IBAN",
    "PL_NRB": "NRB",
    "PL_PARCEL": "DZIALKA",
    "PL_WNI": "WNI",
    "PL_ADDRESS": "ADRES",
    "PL_POSTAL_CODE": "KOD_POCZTOWY",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "TELEFON",
    "IP_ADDRESS": "IP",
    "CREDIT_CARD": "KARTA",
    "PERSON": "OSOBA",
    "LOCATION": "MIEJSCE",
}
PSEUDONYMIZED = frozenset({"PERSON", "LOCATION"})

_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class PiiFinding:
    entity: str
    start: int
    end: int
    score: float


class Analyzer(Protocol):
    def analyze(self, text: str) -> list[PiiFinding]: ...


@dataclass(frozen=True, slots=True)
class ScrubPolicy:
    replace: frozenset[str] = frozenset(TAGS)
    thresholds: dict[str, float] = field(default_factory=dict[str, float])
    default_threshold: float = 0.5

    def threshold(self, entity: str) -> float:
        return self.thresholds.get(entity, self.default_threshold)


DEFAULT_POLICY = ScrubPolicy(
    thresholds={
        # Checksum-validated: a valid match is reliable.
        "PL_PESEL": 0.5,
        "PL_NIP": 0.5,
        "PL_REGON": 0.5,
        "IBAN_CODE": 0.5,
        # Context-dependent: only with supporting words nearby.
        "PL_KRS": 0.4,
        "PL_NRB": 0.4,
        "PL_WNI": 0.4,
        "PL_PARCEL": 0.4,
        # NER: spaCy scores are coarse (0.85 by default in Presidio).
        "PERSON": 0.6,
        "LOCATION": 0.6,
    }
)


@dataclass(frozen=True, slots=True)
class ScrubResult:
    text: str
    findings: list[PiiFinding]
    stats: dict[str, int]

    @property
    def changed(self) -> bool:
        return bool(self.findings)


def _select(findings: list[PiiFinding], policy: ScrubPolicy) -> list[PiiFinding]:
    """Keep findings above their threshold. Overlaps go to the higher score, then longer span."""
    candidates = [
        f
        for f in findings
        if f.entity in policy.replace and f.score >= policy.threshold(f.entity) and f.end > f.start
    ]
    candidates.sort(key=lambda f: (-f.score, -(f.end - f.start), f.start))
    chosen: list[PiiFinding] = []
    for finding in candidates:
        if all(finding.end <= c.start or finding.start >= c.end for c in chosen):
            chosen.append(finding)
    return sorted(chosen, key=lambda f: f.start)


class _Pseudonyms:
    def __init__(self) -> None:
        self._keys: dict[str, list[tuple[tuple[str, ...], int]]] = {}

    @staticmethod
    def _key(surface: str) -> tuple[str, ...]:
        return tuple(token.casefold()[:3] for token in _TOKEN.findall(surface))

    def number(self, entity: str, surface: str) -> int:
        key = self._key(surface) or (surface.casefold(),)
        known = self._keys.setdefault(entity, [])
        for existing, number in known:
            if existing == key:
                return number
        if entity == "PERSON":
            # A lone surname (or "Pan Kowalski") joins a full name with the same last token.
            for existing, number in known:
                if existing[-1] == key[-1] and (len(key) == 1 or len(existing) == 1):
                    return number
        number = len({n for _, n in known}) + 1
        known.append((key, number))
        return number


class PiiScrubber:
    def __init__(self, analyzer: Analyzer) -> None:
        self._analyzer = analyzer

    def scrub(self, text: str, *, policy: ScrubPolicy = DEFAULT_POLICY) -> ScrubResult:
        chosen = _select(self._analyzer.analyze(text), policy)
        pseudonyms = _Pseudonyms()
        pieces: list[str] = []
        cursor = 0
        for finding in chosen:
            pieces.append(text[cursor : finding.start])
            tag = TAGS[finding.entity]
            if finding.entity in PSEUDONYMIZED:
                number = pseudonyms.number(finding.entity, text[finding.start : finding.end])
                pieces.append(f"<{tag}_{number}>")
            else:
                pieces.append(f"<{tag}>")
            cursor = finding.end
        pieces.append(text[cursor:])
        stats = dict(Counter(f.entity for f in chosen))
        return ScrubResult("".join(pieces), chosen, stats)
