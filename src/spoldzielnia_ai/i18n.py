"""The UI language and the server's own user-facing texts (Stage 14, US-08).

Two languages: Polish (the default, always, until the user chooses) and English. A user's
choice lives on the account (``app_user.locale``, NULL = not chosen) and reaches every page
through the ``lang`` cookie, which holds ``pl`` or ``en`` and nothing else (rule R1). Anything
else in it reads as Polish. ``Accept-Language`` is never consulted.

Content (notes, articles, labels) is never translated. Only the fixed texts below are: answers
the server writes itself, never the model. Error ``detail``s stay English machine codes that the
frontend maps to messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from starlette.responses import Response

Locale = Literal["pl", "en"]
LOCALES: tuple[Locale, ...] = ("pl", "en")
DEFAULT_LOCALE: Locale = "pl"
COOKIE = "lang"
COOKIE_MAX_AGE = 31_536_000  # a year

MESSAGES: dict[str, dict[Locale, str]] = {
    "rag.no_official_position": {
        "pl": (
            "Brak oficjalnego stanowiska ekspertów spółdzielni w tej sprawie. Zatwierdzona baza "
            "wiedzy nie zawiera odpowiedzi na to pytanie. Możesz zgłosić je Radzie Ekspertów."
        ),
        "en": (
            "No official position of the cooperative's experts on this matter. The approved "
            "knowledge base does not answer this question. You can refer it to the Expert "
            "Council."
        ),
    },
    "rag.no_notes_answer": {
        "pl": "Twoje notatki nie zawierają informacji pozwalających odpowiedzieć na to pytanie.",
        "en": "Your notes do not contain information that answers this question.",
    },
    "rag.unknown_author": {"pl": "Członek spółdzielni", "en": "Cooperative member"},
    "rag.unknown_expert": {"pl": "Ekspert spółdzielni", "en": "Cooperative expert"},
    "rag.answered_in_polish": {
        "pl": "",
        "en": (
            "*This answer could not be checked in English, so it is given in Polish, the "
            "language of the knowledge base.*"
        ),
    },
    "shield.verbatim_refusal": {
        "pl": (
            "Nie mogę przytoczyć treści bazy wiedzy dosłownie ani w całości. Zapoznaj się "
            "z artykułem w Wiki:"
        ),
        "en": (
            "I cannot reproduce the knowledge base word for word or in full, nor translate it. "
            "Please read the article in the Wiki:"
        ),
    },
}


def parse_locale(value: str | None) -> Locale | None:
    """``pl`` or ``en`` exactly, else None. Never case-folded or trimmed: the cookie is ours."""
    return value if value in LOCALES else None


def locale_or_default(value: str | None) -> Locale:
    return parse_locale(value) or DEFAULT_LOCALE


def set_locale_cookie(response: Response, locale: Locale) -> None:
    """Readable by the page (not HttpOnly): a signed-out visitor's switch sets it in the browser,
    and the Next.js layout reads it for the first paint. It never carries anything but a code."""
    response.set_cookie(
        COOKIE, locale, max_age=COOKIE_MAX_AGE, path="/", secure=True, samesite="lax"
    )


def t(key: str, locale: Locale, **params: object) -> str:
    text = MESSAGES[key][locale]
    return text.format(**params) if params else text
