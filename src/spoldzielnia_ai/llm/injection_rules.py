"""Prompt-injection heuristics, Polish + English (chunk 3.3, layer 2, gap G12).

Text is normalised before matching, so common obfuscations don't help: lower-case, zero-width
and bidi controls removed, diacritics folded (``ł`` → ``l``), Cyrillic/Greek look-alikes mapped to
Latin, letter-spaced words (``z i g n o r u j``) collapsed. Base64 blobs are decoded and
checked too. Matches map back to offsets in the **original** text, for highlighting.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

# U+200B-U+200F, U+202A-U+202E, U+2060-U+2064, U+2066-U+2069, U+FEFF
INVISIBLE = frozenset(
    [
        *range(0x200B, 0x2010),
        *range(0x202A, 0x202F),
        *range(0x2060, 0x2065),
        *range(0x2066, 0x206A),
        0xFEFF,
    ]
)
# Cyrillic/Greek look-alikes → Latin (escaped: the source itself stays unambiguous).
_HOMOGLYPHS = str.maketrans(
    {
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
        "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s",
        "\u043a": "k", "\u043c": "m", "\u0442": "t", "\u0432": "b", "\u043d": "h",
        "\u03bf": "o", "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k",
        "\u03bd": "v", "\u03c1": "p", "\u03c4": "t", "\u03c5": "u",
        "\u0142": "l", "\u0141": "l", "\u00df": "ss",
    }
)  # fmt: skip

RULES: dict[str, str] = {
    # Polish
    "pl_ignore_previous": r"(zignoruj|pomin|zapomnij|olej|nie zwracaj uwagi na)\w*\s+"
    r"(wszystkie\s+|wszelkie\s+|cale\s+)?(poprzedni|wczesniejsz|powyzsz|dotychczasow)\w*\s+"
    r"(instrukcj|polecen|zasad|wytyczn|regul|ustalen)",
    "pl_you_are_now": r"\b(jestes|bedziesz)\s+teraz\b|\bod teraz\s+(jestes|dzialasz|odpowiadasz)",
    "pl_new_instructions": r"\b(nowe|zmienione|prawdziwe)\s+(instrukcje|polecenia|zasady)\b",
    "pl_reveal": r"\b(ujawnij|pokaz|wypisz|zdradz|podaj)\s+(\w+\s+){0,3}"
    r"(prompt|instrukcj|dane|hasl|klucz|system)",
    "pl_system_prompt": r"\bprompt\w*\s+systemow\w*|\bsystemow\w*\s+prompt",
    "pl_act_as": r"\b(udawaj|zachowuj sie jak|wciel sie w)\b",
    # English
    "en_ignore_previous": r"\b(ignore|disregard|forget|skip)\s+(all\s+|any\s+|the\s+)?"
    r"(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules|directions)",
    "en_you_are_now": r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\s+you\b",
    "en_system_prompt": r"\bsystem\s+prompt\b|\bdeveloper\s+mode\b|\bjailbreak\b",
    "en_reveal": r"\b(reveal|print|show|output|leak)\s+(\w+\s+){0,3}"
    r"(prompt|instructions|secrets?|keys?)",
    "en_new_instructions": r"\bnew\s+instructions?\b|\boverride\s+(the\s+)?(rules|instructions)",
    # Role/control tags used to fake a conversation turn.
    "role_tags": r"<\s*/?\s*(system|assistant|user|im_start|im_end)\s*>|\[/?inst\]|<<\s*sys\s*>>"
    r"|###\s*(instruction|system|response)\b|<\|(im_start|im_end|system|endoftext)\|>",
}  # fmt: skip
# `\w*`: extend a stem match ("instrukcj") to the whole word, for highlighting.
_COMPILED = {name: re.compile(f"(?:{pattern})\\w*") for name, pattern in RULES.items()}
_BASE64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
_SPACED = re.compile(r"(?<!\S)(?:\w[ \t._\-]){3,}\w(?!\S)")


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule: str
    start: int  # offsets in the original text
    end: int
    via: str  # "text" | "base64"


def _fold(char: str) -> str:
    char = char.translate(_HOMOGLYPHS)
    decomposed = unicodedata.normalize("NFKD", char)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize(text: str) -> tuple[str, list[int]]:
    """Normalised text and, for each of its characters, the index in ``text``."""
    out: list[str] = []
    index: list[int] = []
    for i, char in enumerate(text):
        if ord(char) in INVISIBLE or unicodedata.category(char) == "Cf":
            continue
        for folded in _fold(char):
            out.append(folded)
            index.append(i)
    normalized = "".join(out)
    # Collapse letter-spaced words ("z i g n o r u j" → "zignoruj"), keeping the index map.
    collapsed: list[str] = []
    collapsed_index: list[int] = []
    cursor = 0
    for match in _SPACED.finditer(normalized):
        collapsed.extend(normalized[cursor : match.start()])
        collapsed_index.extend(index[cursor : match.start()])
        for j in range(match.start(), match.end()):
            if normalized[j].isalnum():
                collapsed.append(normalized[j])
                collapsed_index.append(index[j])
        cursor = match.end()
    collapsed.extend(normalized[cursor:])
    collapsed_index.extend(index[cursor:])
    return "".join(collapsed), collapsed_index


def count_invisible(text: str) -> int:
    return sum(1 for char in text if ord(char) in INVISIBLE)


def strip_invisible(text: str) -> str:
    return "".join(char for char in text if ord(char) not in INVISIBLE)


def find_rules(text: str) -> list[RuleMatch]:
    normalized, index = normalize(text)
    matches: list[RuleMatch] = []
    for name, pattern in _COMPILED.items():
        for match in pattern.finditer(normalized):
            if match.end() > match.start():
                matches.append(
                    RuleMatch(name, index[match.start()], index[match.end() - 1] + 1, "text")
                )
    for blob in _BASE64.finditer(text):
        try:
            decoded = base64.b64decode(blob.group(0), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        inner, _ = normalize(decoded)
        for name, pattern in _COMPILED.items():
            if pattern.search(inner):
                matches.append(RuleMatch(name, blob.start(), blob.end(), "base64"))
                break
    return sorted(matches, key=lambda m: (m.start, m.rule))
