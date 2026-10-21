"""Name normalisation for domains and labels: slugs and accent-folded search keys.

Done in Python (not Postgres ``unaccent``): the extension needs a superuser on some installs,
and the vocabulary is small enough that a folded ``ILIKE`` column is all search needs.
"""

from __future__ import annotations

import re
import unicodedata

# NFKD leaves these as single code points without a combining mark.
_FOLD = str.maketrans({"ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "ß": "ss", "đ": "d", "Đ": "d"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACES = re.compile(r"\s+")


def fold(value: str) -> str:
    """Lower case, without diacritics, single spaces: "  Matka  Pszczoła " → "matka pszczola"."""
    decomposed = unicodedata.normalize("NFKD", value.translate(_FOLD))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SPACES.sub(" ", stripped.casefold()).strip()


def clean_name(value: str) -> str:
    """The displayed name: trimmed, inner whitespace collapsed, case kept."""
    return _SPACES.sub(" ", value).strip()


def slugify(value: str) -> str:
    """URL-safe key: "Matka pszczela (Apis)" → "matka-pszczela-apis". Empty without letters."""
    return _NON_ALNUM.sub("-", fold(value)).strip("-")[:100]


def search_key(name: str, synonyms: list[str]) -> str:
    """What label search matches against: the folded name and synonyms, one per line."""
    return "\n".join(dict.fromkeys(fold(v) for v in (name, *synonyms) if fold(v)))
