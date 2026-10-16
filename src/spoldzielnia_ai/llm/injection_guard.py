"""Prompt-injection verdicts (chunk 3.3): heuristics + classifier + document findings.

``InjectionVerdict.to_flags()`` is what is stored in ``note_derivative.injection_flags``
(plaintext JSONB inside the encrypted private zone). It therefore holds **kinds, rule names,
offsets and scores only, never matched text.** Offsets refer to the stored (decrypted)
derivative content, so the owner's UI can highlight the spans.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from spoldzielnia_ai.llm.injection_rules import count_invisible, find_rules

if TYPE_CHECKING:
    from spoldzielnia_ai.llm.injection_model import WindowScore

FlagKind = Literal["HEURISTIC", "CLASSIFIER", "HIDDEN_TEXT", "INVISIBLE_CHARS"]

QUARANTINE_THRESHOLD = 0.8
CLASSIFIER_FLAG_THRESHOLD = 0.5
# The classifier is English-trained. On Polish it misfires: benign Polish typed without
# diacritics ("podaje sie zima, gdy nie ma czerwiu") scored 0.99, while the same sentence with
# diacritics scored 0.0. On Polish windows its flag is therefore advisory: recorded and shown,
# but capped below the quarantine threshold. The Polish heuristics stay authoritative there.
# Real Polish injections score 1.0 there too, but they are caught by the rules.
POLISH_ADVISORY_CAP = 0.75
# Uncorroborated classifier scores quarantine only from LLM-Guard's own default threshold for
# this model (0.92). Below that they are advisory. Imperative English such as example.org's
# "avoid relying on it for testing" already scores ~0.94.
CLASSIFIER_QUARANTINE_THRESHOLD = 0.92
_POLISH_FUNCTION_WORDS = frozenset(
    {
        "i", "w", "z", "na", "nie", "sie", "jest", "to", "do", "ze", "o", "a", "gdy", "jak",
        "po", "dla", "od", "przez", "oraz", "tak", "ale", "czy", "juz", "ma", "sa", "przy",
        "pod", "nad", "za", "bo", "tez", "lub", "jego", "jej", "ich", "mnie", "mi", "ten",
        "ta", "te", "tego", "tylko", "bardzo", "kiedy",
    }
)  # fmt: skip
_POLISH_LETTERS = frozenset("ąćęłńóśźż")
RULE_SCORES: dict[str, float] = {
    "pl_ignore_previous": 0.95,
    "en_ignore_previous": 0.95,
    "pl_system_prompt": 0.9,
    "en_system_prompt": 0.9,
    "pl_reveal": 0.85,
    "en_reveal": 0.85,
    "pl_new_instructions": 0.85,
    "en_new_instructions": 0.85,
    "pl_you_are_now": 0.85,
    "en_you_are_now": 0.85,
    "role_tags": 0.8,
    "pl_act_as": 0.5,
}
FLAGS_VERSION = 1


class Classifier(Protocol):
    def score(self, text: str) -> list[WindowScore]: ...


@dataclass(frozen=True, slots=True)
class InjectionFlag:
    kind: FlagKind
    score: float
    rule: str | None = None
    start: int | None = None
    end: int | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind, "score": round(self.score, 4)}
        if self.rule is not None:
            data["rule"] = self.rule
        if self.start is not None and self.end is not None:
            data["start"], data["end"] = self.start, self.end
        return data


@dataclass(frozen=True, slots=True)
class InjectionVerdict:
    score: float
    flags: list[InjectionFlag] = field(default_factory=list[InjectionFlag])

    @property
    def quarantine(self) -> bool:
        return self.score >= QUARANTINE_THRESHOLD

    def kinds(self) -> list[str]:
        return sorted({flag.kind for flag in self.flags})

    def to_flags(self) -> dict[str, Any]:
        return {
            "version": FLAGS_VERSION,
            "scanned_at": dt.datetime.now(dt.UTC).isoformat(),
            "score": round(self.score, 4),
            "quarantine": self.quarantine,
            "flags": [flag.to_json() for flag in self.flags],
        }


def document_flags(stored: dict[str, Any]) -> list[InjectionFlag]:
    """Flags recorded while parsing (hidden text, invisible characters), from the derivative's
    ``injection_flags["document"]``."""
    document = cast("dict[str, Any]", stored.get("document") or {})
    flags: list[InjectionFlag] = []
    for span in cast("list[dict[str, Any]]", document.get("hidden_text", [])):
        # Removed text that read like an instruction is a strong signal by itself.
        score = 0.95 if span.get("instruction_like") else 0.6
        flags.append(InjectionFlag("HIDDEN_TEXT", score, rule=str(span.get("reason"))))
    if int(document.get("invisible_chars", 0)):
        flags.append(InjectionFlag("INVISIBLE_CHARS", 0.4))
    return flags


def is_polish(text: str) -> bool:
    """Cheap language guess: Polish diacritics, or a high share of Polish function words."""
    lowered = text.lower()
    if any(ch in _POLISH_LETTERS for ch in lowered):
        return True
    words = [w for w in (normalize_word(t) for t in lowered.split()) if w]
    if len(words) < 3:
        return False
    return sum(w in _POLISH_FUNCTION_WORDS for w in words) / len(words) >= 0.15


def normalize_word(token: str) -> str:
    return "".join(ch for ch in token if ch.isalpha())


class InjectionGuard:
    def __init__(self, classifier: Classifier | None = None) -> None:
        self._classifier = classifier

    def scan(self, text: str, extra: list[InjectionFlag] | None = None) -> InjectionVerdict:
        flags = [
            InjectionFlag("HEURISTIC", RULE_SCORES.get(m.rule, 0.8), m.rule, m.start, m.end)
            for m in find_rules(text)
        ]
        if count_invisible(text):
            flags.append(InjectionFlag("INVISIBLE_CHARS", 0.4))
        if self._classifier is not None:
            for window in self._classifier.score(text):
                if window.score < CLASSIFIER_FLAG_THRESHOLD:
                    continue
                score, rule = window.score, "deberta-v3-prompt-injection"
                if is_polish(text[window.start : window.end]):
                    score, rule = min(score, POLISH_ADVISORY_CAP), f"{rule}:advisory-pl"
                elif score < CLASSIFIER_QUARANTINE_THRESHOLD:
                    score, rule = min(score, POLISH_ADVISORY_CAP), f"{rule}:advisory"
                flags.append(InjectionFlag("CLASSIFIER", score, rule, window.start, window.end))
        flags.extend(extra or [])
        return InjectionVerdict(max((f.score for f in flags), default=0.0), flags)
