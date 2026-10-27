"""``Watermarker``: deterministic, per-account lexical watermark of answers (5.5, gap G13).

An LLM can't be trusted to apply a watermark consistently, so this is post-processing. Every
**slot** is a pair of interchangeable forms of the answer's language (Polish, or English since
14.5: ``PAIRS_EN``, slots prefixed ``en:``) such as ``należy/trzeba``, ``ponadto/dodatkowo``,
``moreover/furthermore``, or a structural choice (bullet ``-``/``*``, numbering ``1.``/``1)``).
For each account the choice in each slot is fixed by

    bit = HMAC_SHA256(watermark_key, user_id || slot)[0] & 1

so all answers of one account use the same variants (keyed on the account, not the request:
attribution needs consistency across answers).

**Detection** (``detect``): for each candidate account, every slot present in the text is one
observation (its majority variant; repeats of one word count once), agreeing with the
account's bit or not. Unrelated accounts agree on half of the slots by chance; the binomial
z-score ``(agree - n/2) / sqrt(n/4)`` singles out the account the text came from.

Never edited: code spans and blocks, citation markers (``[S1]``, ``[2]``), URLs, numbers
(no slot contains digits). Capitalisation of the replaced word is kept.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from spoldzielnia_ai.core.settings import ShieldSettings

# Interchangeable surface forms (same gender, number, case and government). Pairs whose
# meaning could shift in beekeeping advice (dosages, "w celu" …) are deliberately left out.
# Every form appears once in the table and no phrase contains another form (a test checks).
PAIRS: tuple[tuple[str, str], ...] = (
    # Connectives and adverbs
    ("ponadto", "dodatkowo"),
    ("należy", "trzeba"),
    ("jednak", "jednakże"),
    ("również", "także"),
    ("aby", "żeby"),
    ("zwykle", "zazwyczaj"),
    ("często", "nierzadko"),
    ("wówczas", "wtedy"),
    ("gdyż", "ponieważ"),
    ("następnie", "potem"),
    ("zatem", "więc"),
    ("natychmiast", "niezwłocznie"),
    ("regularnie", "systematycznie"),
    ("dokładnie", "starannie"),
    ("głównie", "przeważnie"),
    ("szczególnie", "zwłaszcza"),
    ("wyłącznie", "jedynie"),
    ("prawie", "niemal"),
    ("bardzo", "niezwykle"),
    ("szybko", "prędko"),
    ("powoli", "wolno"),
    ("nadal", "wciąż"),
    ("obecnie", "aktualnie"),
    ("dotychczas", "dotąd"),
    ("wcześniej", "uprzednio"),
    ("oczywiście", "naturalnie"),
    ("niekiedy", "czasem"),
    ("dość", "dosyć"),
    ("znacznie", "wyraźnie"),
    ("około", "blisko"),
    ("całkowicie", "zupełnie"),
    ("częściowo", "po części"),
    ("bezpośrednio", "wprost"),
    ("przeciętnie", "średnio"),
    ("skutecznie", "efektywnie"),
    ("odpowiednio", "stosownie"),
    ("koniecznie", "bezwzględnie"),
    ("ostatecznie", "finalnie"),
    ("wstępnie", "początkowo"),
    ("stopniowo", "sukcesywnie"),
    ("dodatkowy", "kolejny"),
    # Multi-word connectives
    ("na przykład", "przykładowo"),
    ("przede wszystkim", "w pierwszej kolejności"),
    ("w przypadku", "w razie"),
    ("mniej więcej", "w przybliżeniu"),
    ("co najmniej", "przynajmniej"),
    ("w ten sposób", "tym sposobem"),
    ("dlatego", "z tego względu"),
    ("na koniec", "na zakończenie"),
    ("ze względu na", "z uwagi na"),
    ("w związku z tym", "w konsekwencji"),
    ("mimo to", "pomimo to"),
    ("na bieżąco", "stale"),
    ("od czasu do czasu", "okazjonalnie"),
    ("w efekcie", "w rezultacie"),
    ("z kolei", "natomiast"),
    # Verbs (same person, tense and case government)
    ("stosować", "używać"),
    ("stosuje", "używa"),
    ("stosują", "używają"),
    ("stosuj", "używaj"),
    ("wykonać", "przeprowadzić"),
    ("wykonuje", "przeprowadza"),
    ("wykonują", "przeprowadzają"),
    ("sprawdzić", "skontrolować"),
    ("sprawdza", "kontroluje"),
    ("sprawdzać", "kontrolować"),
    ("sprawdź", "skontroluj"),
    ("zapewnić", "zagwarantować"),
    ("zapewnia", "gwarantuje"),
    ("pozwala", "umożliwia"),
    ("pozwalają", "umożliwiają"),
    ("rozpocząć", "zacząć"),
    ("rozpoczyna", "zaczyna"),
    ("zakończyć", "skończyć"),
    ("zmniejszyć", "ograniczyć"),
    ("zmniejsza", "ogranicza"),
    ("zwiększyć", "podnieść"),
    ("zwiększa", "podnosi"),
    ("uzyskać", "otrzymać"),
    ("uzyskuje", "otrzymuje"),
    ("obejmuje", "zawiera"),
    ("obejmują", "zawierają"),
    ("dotyczy", "odnosi się do"),
    ("wymaga", "potrzebuje"),
    ("zaleca się", "rekomenduje się"),
    ("warto", "opłaca się"),
    ("przygotować", "sporządzić"),
    # Adjectives (gender, number and case kept)
    ("ważny", "istotny"),
    ("ważna", "istotna"),
    ("ważne", "istotne"),
    ("ważnych", "istotnych"),
    ("ważnym", "istotnym"),
    ("ważną", "istotną"),
    ("duży", "spory"),
    ("duża", "spora"),
    ("duże", "spore"),
    ("dużą", "sporą"),
    ("dużych", "sporych"),
    ("mały", "niewielki"),
    ("mała", "niewielka"),
    ("małe", "niewielkie"),
    ("małą", "niewielką"),
    ("małych", "niewielkich"),
    ("kluczowy", "zasadniczy"),
    ("kluczowa", "zasadnicza"),
    ("kluczowe", "zasadnicze"),
    ("odpowiedni", "właściwy"),
    ("odpowiednia", "właściwa"),
    ("odpowiednie", "właściwe"),
    ("odpowiednią", "właściwą"),
    ("odpowiednich", "właściwych"),
    ("skuteczny", "efektywny"),
    ("skuteczna", "efektywna"),
    ("skuteczne", "efektywne"),
    ("silny", "mocny"),
    ("silna", "mocna"),
    ("silne", "mocne"),
    ("prosty", "łatwy"),
    ("prosta", "łatwa"),
    ("proste", "łatwe"),
    ("konieczny", "niezbędny"),
    ("konieczna", "niezbędna"),
    ("konieczne", "niezbędne"),
    ("różne", "rozmaite"),
    ("różnych", "rozmaitych"),
    ("główny", "podstawowy"),
    ("główna", "podstawowa"),
    ("główne", "podstawowe"),
    ("głównym", "podstawowym"),
    ("szkodliwy", "niekorzystny"),
    ("szkodliwe", "niekorzystne"),
    # Nouns (same gender)
    ("problem", "kłopot"),
    ("problemu", "kłopotu"),
    ("problemy", "kłopoty"),
    ("problemów", "kłopotów"),
    ("objaw", "symptom"),
    ("objawy", "symptomy"),
    ("objawów", "symptomów"),
    ("rodzaj", "typ"),
    ("rodzaje", "typy"),
    ("rodzaju", "typu"),
    ("wynik", "rezultat"),
    ("wyniki", "rezultaty"),
    ("wyników", "rezultatów"),
    ("wyniku", "rezultatu"),
    ("efekt", "skutek"),
    ("efekty", "skutki"),
    ("efektu", "skutku"),
    ("kontrola", "inspekcja"),
    ("kontroli", "inspekcji"),
    ("kontrolę", "inspekcję"),
    ("zasada", "reguła"),
    ("zasady", "reguły"),
    ("zasad", "reguł"),
    ("metoda", "technika"),
    ("metody", "techniki"),
    ("metod", "technik"),
    ("metodą", "techniką"),
    ("ryzyko", "zagrożenie"),
    ("ryzyka", "zagrożenia"),
    ("działanie", "postępowanie"),
    ("działania", "postępowania"),
    ("preparat", "środek"),
    ("preparaty", "środki"),
    ("preparatu", "środka"),
    ("preparatów", "środków"),
    ("zalecenie", "wskazanie"),
    ("zalecenia", "wskazania"),
    ("etap", "faza"),
    ("etapie", "fazie"),
    ("sezon", "okres pszczelarski"),
    ("sezonie", "okresie pszczelarskim"),
)
# English answers (14.5). Stricter than the Polish table: English words are often both a noun
# and a verb ("check", "start", "result", "needs"), and a swap must never change the grammar
# or the meaning, so only forms with one part of speech and one sense in advice are used. Fewer
# slots, so English answers carry less watermark each: detection needs a few more of them.
PAIRS_EN: tuple[tuple[str, str], ...] = (
    # Connectives and adverbs
    ("moreover", "furthermore"),
    ("therefore", "thus"),
    ("usually", "typically"),
    ("often", "frequently"),
    ("immediately", "promptly"),
    ("regularly", "routinely"),
    ("carefully", "thoroughly"),
    ("mainly", "mostly"),
    ("especially", "particularly"),
    ("almost", "nearly"),
    ("quickly", "rapidly"),
    ("currently", "presently"),
    ("sometimes", "occasionally"),
    ("significantly", "considerably"),
    ("approximately", "roughly"),
    ("completely", "entirely"),
    ("partially", "partly"),
    ("appropriately", "suitably"),
    ("eventually", "ultimately"),
    ("initially", "at first"),
    ("gradually", "progressively"),
    ("for example", "for instance"),
    ("above all", "first and foremost"),
    ("in case of", "in the event of"),
    # Verbs
    ("checked", "inspected"),
    ("ensure", "make sure"),
    ("ensures", "makes sure"),
    ("allows", "enables"),
    ("begins", "starts"),
    ("contains", "includes"),
    ("recommended", "advised"),
    ("obtain", "acquire"),
    ("assess", "evaluate"),
    ("assesses", "evaluates"),
    # Adjectives
    ("important", "significant"),
    ("large", "big"),
    ("appropriate", "suitable"),
    ("strong", "robust"),
    ("simple", "easy"),
    ("various", "diverse"),
    ("main", "primary"),
    ("harmful", "damaging"),
    ("rapid", "quick"),
    ("sufficient", "adequate"),
    ("additional", "extra"),
    ("typical", "usual"),
    ("dangerous", "hazardous"),
    ("visible", "noticeable"),
    # Nouns
    ("method", "technique"),
    ("methods", "techniques"),
    ("recommendation", "guideline"),
    ("recommendations", "guidelines"),
    ("types", "kinds"),
)
STRUCTURAL = ("bullet", "numbering")

_PROTECTED = re.compile(
    r"```.*?```"  # code blocks
    r"|`[^`\n]*`"  # inline code
    r"|\[(?:S?\d{1,3})\]"  # citation markers
    r"|\]\([^)\s]*\)"  # Markdown link targets
    r"|https?://\S+",  # bare URLs
    re.S,
)
_BULLET = re.compile(r"^([ \t]*)([-*])(?=[ \t]+\S)", re.M)
_NUMBERED = re.compile(r"^([ \t]*\d{1,3})([.)])(?=[ \t]+\S)", re.M)


@dataclass(frozen=True, slots=True)
class _Table:
    """One language's slots. A slot's name is its first form, with the table's prefix."""

    pairs: tuple[tuple[str, str], ...]
    prefix: str
    slot_of: dict[str, tuple[int, int]]  # lower-case form → (pair index, variant)
    forms: re.Pattern[str]

    @classmethod
    def build(cls, pairs: tuple[tuple[str, str], ...], prefix: str) -> _Table:
        slot_of = {form: (i, v) for i, pair in enumerate(pairs) for v, form in enumerate(pair)}
        longest_first = sorted(slot_of, key=len, reverse=True)
        forms = re.compile(
            r"(?<!\w)(" + "|".join(re.escape(f) for f in longest_first) + r")(?!\w)",
            re.IGNORECASE,
        )
        return cls(pairs, prefix, slot_of, forms)

    def slot(self, pair: int) -> str:
        return self.prefix + self.pairs[pair][0]


TABLES: dict[str, _Table] = {"pl": _Table.build(PAIRS, ""), "en": _Table.build(PAIRS_EN, "en:")}
SLOT_OF = TABLES["pl"].slot_of


def _match_case(original: str, replacement: str) -> str:
    if len(original) > 1 and original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _segments(text: str) -> list[tuple[bool, str]]:
    """(editable, text) pieces covering ``text``."""
    pieces: list[tuple[bool, str]] = []
    last = 0
    for m in _PROTECTED.finditer(text):
        pieces.append((True, text[last : m.start()]))
        pieces.append((False, m.group(0)))
        last = m.end()
    pieces.append((True, text[last:]))
    return pieces


@dataclass(frozen=True, slots=True)
class Attribution:
    user_id: UUID
    z: float
    agree: int
    slots: int


class Watermarker:
    def __init__(self, key: bytes) -> None:
        if not key:
            raise ValueError("watermark key is empty")
        self._key = key

    @classmethod
    def from_settings(cls, settings: ShieldSettings) -> Watermarker | None:
        key = settings.watermark_key
        if key is None or not key.get_secret_value().strip():
            return None
        return cls(key.get_secret_value().strip().encode())

    def bit(self, user_id: UUID, slot: str) -> int:
        digest = hmac.new(self._key, f"{user_id}|{slot}".encode(), hashlib.sha256).digest()
        return digest[0] & 1

    def apply(self, text: str, user_id: UUID, language: str = "pl") -> str:
        """``text`` with the account's variants of ``language``'s slots (Polish by default)."""
        table = TABLES[language]
        bullet = "-*"[self.bit(user_id, "bullet")]
        numbering = ".)"[self.bit(user_id, "numbering")]

        def word(m: re.Match[str]) -> str:
            pair, _ = table.slot_of[m.group(1).lower()]
            choice = table.pairs[pair][self.bit(user_id, table.slot(pair))]
            return _match_case(m.group(1), choice)

        out: list[str] = []
        for editable, piece in _segments(text):
            if not editable:
                out.append(piece)
                continue
            piece = table.forms.sub(word, piece)
            piece = _BULLET.sub(lambda m: m.group(1) + bullet, piece)
            piece = _NUMBERED.sub(lambda m: m.group(1) + numbering, piece)
            out.append(piece)
        return "".join(out)

    @staticmethod
    def observations(text: str) -> dict[str, int]:
        """Slot → majority variant used in ``text`` (ties and absent slots left out). Both
        languages' slots are observed: a text does not say which one it was written in."""
        counts: dict[str, list[int]] = {}
        editable = " \n".join(piece for ok, piece in _segments(text) if ok)
        for table in TABLES.values():
            for m in table.forms.finditer(editable):
                pair, variant = table.slot_of[m.group(1).lower()]
                counts.setdefault(table.slot(pair), [0, 0])[variant] += 1
        for slot, pattern, symbols in (("bullet", _BULLET, "-*"), ("numbering", _NUMBERED, ".)")):
            for m in pattern.finditer(editable):
                counts.setdefault(slot, [0, 0])[symbols.index(m.group(2))] += 1
        return {slot: int(c[1] > c[0]) for slot, c in counts.items() if c[0] != c[1]}

    def score(self, text: str, user_id: UUID) -> Attribution:
        seen = self.observations(text)
        agree = sum(1 for slot, variant in seen.items() if self.bit(user_id, slot) == variant)
        n = len(seen)
        z = (agree - n / 2) / math.sqrt(n / 4) if n else 0.0
        return Attribution(user_id, z, agree, n)

    def detect(self, text: str, candidates: Iterable[UUID]) -> list[Attribution]:
        """Candidates ranked by z-score, highest first."""
        return sorted((self.score(text, c) for c in candidates), key=lambda a: a.z, reverse=True)


def attributed(attributions: Sequence[Attribution], threshold: float) -> list[Attribution]:
    return [a for a in attributions if a.z > threshold]
