"""Chunk 14.1: the server's catalogue of user-facing texts and the ``lang`` cookie's values."""

from __future__ import annotations

import string

import pytest
from starlette.responses import Response

from spoldzielnia_ai.i18n import (
    COOKIE,
    LOCALES,
    MESSAGES,
    locale_or_default,
    parse_locale,
    set_locale_cookie,
    t,
)


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_every_text_exists_in_both_languages_with_the_same_placeholders(key: str) -> None:
    texts = MESSAGES[key]
    assert set(texts) == set(LOCALES)
    assert len({frozenset(_placeholders(text)) for text in texts.values()}) == 1


@pytest.mark.parametrize("key", sorted(set(MESSAGES) - {"rag.answered_in_polish"}))
def test_english_is_a_translation(key: str) -> None:
    assert MESSAGES[key]["en"]
    assert MESSAGES[key]["en"] != MESSAGES[key]["pl"]


def test_a_polish_answer_needs_no_note() -> None:
    assert t("rag.answered_in_polish", "pl") == ""
    assert "Polish" in t("rag.answered_in_polish", "en")


@pytest.mark.parametrize(
    "value", [None, "", "de", "EN", " en", "en ", "pl-PL", "en;q=1", "<script>", "en\x00"]
)
def test_anything_but_the_two_codes_is_polish(value: str | None) -> None:
    assert parse_locale(value) is None
    assert locale_or_default(value) == "pl"


@pytest.mark.parametrize("value", ["pl", "en"])
def test_the_two_codes(value: str) -> None:
    assert parse_locale(value) == value


def test_the_cookie_carries_the_code_only() -> None:
    response = Response()
    set_locale_cookie(response, "en")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{COOKIE}=en;")
    assert "Max-Age=31536000" in header
    assert "Path=/" in header
    assert "Secure" in header
    assert "SameSite=lax" in header
    assert "HttpOnly" not in header  # a signed-out visitor's switch sets it from the page
