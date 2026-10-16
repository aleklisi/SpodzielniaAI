"""Polish identifiers: patterns, normalisation and checksum validation (pure Python).

No Presidio here: the recognizers (3.2) wrap these functions, and the post-LLM check of
promotion (3.5) runs them over every digit run. Checksums keep precision high. A random
11-digit number is a valid PESEL with probability ~1/10 *and* must encode a real date.

- PESEL: weights 1,3,7,9,1,3,7,9,1,3, check = (10 - sum % 10) % 10, and a real birth date
  (the century is encoded in the month: +80 1800s, +0 1900s, +20 2000s, +40, +60).
- NIP: weights 6,5,7,2,3,4,5,6,7, sum % 11 (10 → invalid). Optional ``PL`` prefix, separators.
- REGON: 9 digits (weights 8,9,2,3,4,5,6,7) or 14 digits (2,4,8,5,0,9,7,3,6,1,2,4,8),
  mod 11 (10 → 0). A 14-digit REGON extends a valid 9-digit one.
- KRS: 10 digits, format only. Callers require context (``KRS``, ``rejestr``).
- IBAN: ISO 13616 / ISO 7064 mod 97 = 1, ``PL`` = 28 characters. NRB = 26 digits, ``PL``
  implied.
- Działka (land parcel): TERYT ``WWPPGG_R.OOOO.NR[/NR]``, voivodeship code even 02-32,
  unit type 1-9.
- WNI (apiary veterinary number): format unconfirmed (gap G16), so callers require context.
"""

from __future__ import annotations

import datetime as dt
import re

# --- Normalisation -------------------------------------------------------------------------

_SEPARATORS = re.compile(r"[\s\-./]")


def digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _weighted(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False))


# --- PESEL ---------------------------------------------------------------------------------

PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
# Month offset → century.
_PESEL_CENTURY = {0: 1900, 20: 2000, 40: 2100, 60: 2200, 80: 1800}
PESEL_PATTERN = r"(?<!\d)\d{11}(?!\d)"


def normalize_pesel(value: str) -> str:
    return _SEPARATORS.sub("", value.strip())


def pesel_birth_date(value: str) -> dt.date | None:
    pesel = normalize_pesel(value)
    if len(pesel) != 11 or not pesel.isdigit():
        return None
    year, month, day = int(pesel[0:2]), int(pesel[2:4]), int(pesel[4:6])
    offset = (month // 20) * 20
    if offset not in _PESEL_CENTURY:
        return None
    try:
        return dt.date(_PESEL_CENTURY[offset] + year, month - offset, day)
    except ValueError:
        return None


def is_valid_pesel(value: str) -> bool:
    pesel = normalize_pesel(value)
    if len(pesel) != 11 or not pesel.isdigit() or pesel_birth_date(pesel) is None:
        return False
    check = (10 - _weighted(pesel[:10], PESEL_WEIGHTS) % 10) % 10
    return check == int(pesel[10])


# --- NIP -----------------------------------------------------------------------------------

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
NIP_PATTERNS = (
    r"(?<![\dA-Z])(?:PL[ ]?)?\d{3}-?\d{3}-?\d{2}-?\d{2}(?!\d)",
    r"(?<![\dA-Z])(?:PL[ ]?)?\d{3}-?\d{2}-?\d{2}-?\d{3}(?!\d)",
)


def normalize_nip(value: str) -> str:
    text = value.strip().upper()
    text = text.removeprefix("PL")
    return _SEPARATORS.sub("", text)


def is_valid_nip(value: str) -> bool:
    nip = normalize_nip(value)
    if len(nip) != 10 or not nip.isdigit() or len(set(nip)) == 1:
        return False
    check = _weighted(nip[:9], NIP_WEIGHTS) % 11
    return check != 10 and check == int(nip[9])


# --- REGON ---------------------------------------------------------------------------------

REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
REGON_PATTERN = r"(?<!\d)(?:\d{14}|\d{9})(?!\d)"


def normalize_regon(value: str) -> str:
    return _SEPARATORS.sub("", value.strip())


def is_valid_regon(value: str) -> bool:
    regon = normalize_regon(value)
    if not regon.isdigit() or len(set(regon)) == 1:
        return False
    if len(regon) == 9:
        weights = REGON9_WEIGHTS
    elif len(regon) == 14:
        if not is_valid_regon(regon[:9]):  # a 14-digit REGON extends a valid 9-digit one
            return False
        weights = REGON14_WEIGHTS
    else:
        return False
    check = _weighted(regon[:-1], weights) % 11 % 10
    return check == int(regon[-1])


# --- KRS -----------------------------------------------------------------------------------

KRS_PATTERN = r"(?<!\d)\d{10}(?!\d)"
KRS_CONTEXT = ("krs", "rejestr", "rejestru", "sądowy", "sadowy")


def normalize_krs(value: str) -> str:
    return digits_only(value)


def is_valid_krs(value: str) -> bool:
    krs = normalize_krs(value)
    return len(krs) == 10 and krs != "0" * 10


# --- IBAN / NRB ----------------------------------------------------------------------------

IBAN_PL_PATTERN = r"(?<![A-Z0-9])PL[ ]?\d{2}(?:[ ]?\d{4}){6}(?!\d)"
IBAN_PATTERN = (
    r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?(?![A-Z0-9])"
)
NRB_PATTERN = r"(?<!\d)\d{2}(?:[ ]?\d{4}){6}(?!\d)"
NRB_CONTEXT = ("konto", "konta", "rachunek", "rachunku", "nrb", "przelew", "iban", "bank")
_IBAN_LENGTHS = {"PL": 28, "DE": 22, "GB": 22, "FR": 27, "CZ": 24, "SK": 24, "LT": 20, "UA": 29}


def normalize_iban(value: str) -> str:
    return re.sub(r"\s", "", value.strip().upper())


def _mod97(iban: str) -> int:
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97


def is_valid_iban(value: str) -> bool:
    iban = normalize_iban(value)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    expected = _IBAN_LENGTHS.get(iban[:2])
    if expected is not None and len(iban) != expected:
        return False
    return _mod97(iban) == 1


def is_valid_nrb(value: str) -> bool:
    """Polish account number without country code (26 digits): an implied ``PL`` IBAN."""
    nrb = digits_only(value)
    return len(nrb) == 26 and is_valid_iban("PL" + nrb)


# --- Działka ewidencyjna (land parcel) -----------------------------------------------------

PARCEL_TERYT_PATTERN = r"(?<!\d)\d{6}_\d\.\d{4}\.\d+(?:/\d+)?(?!\d)"
PARCEL_SHORT_PATTERN = (
    r"(?i)(?:dz\.?|działk[ai]|dzialk[ai])\s*(?:nr\.?|ewid\.?)?\s*\d{1,5}(?:/\d{1,4})?"
)
PARCEL_CONTEXT = ("działka", "działki", "dzialka", "ewidencyjna", "obręb", "obreb", "nr dz")


def is_valid_parcel_id(value: str) -> bool:
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})_(\d)\.(\d{4})\.(\d+)(?:/(\d+))?", value.strip())
    if match is None:
        return False
    voivodeship, unit_type = int(match.group(1)), int(match.group(4))
    return 2 <= voivodeship <= 32 and voivodeship % 2 == 0 and 1 <= unit_type <= 9


# --- WNI (apiary veterinary identification number) -----------------------------------------

WNI_PATTERN = r"(?<!\d)\d{8,10}(?!\d)"
WNI_CONTEXT = ("weterynaryjny", "weterynaryjnego", "wni", "pasieka", "pasieki", "piw", "pasiecznik")


def looks_like_wni(value: str) -> bool:
    """Format only. Gap G16: the real format is unconfirmed. Callers require context words."""
    return 8 <= len(digits_only(value)) <= 10


# --- Digit runs (post-LLM re-check in promotion) -------------------------------------------

_DIGIT_RUN = re.compile(r"(?:PL[ ]?)?\d[\d \-]{7,32}\d")


def find_checksum_identifiers(text: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) of every substring that validates as PESEL, NIP, REGON or IBAN/NRB.

    Context-free, so only checksum-validated kinds. Used to assert that generated text
    contains no identifiers, even when the LLM reformatted them.
    """
    found: list[tuple[str, int, int]] = []
    for match in _DIGIT_RUN.finditer(text):
        candidate = match.group(0)
        digits = digits_only(candidate)
        if candidate.upper().startswith("PL") and is_valid_iban(candidate):
            found.append(("IBAN", match.start(), match.end()))
        elif len(digits) == 26 and is_valid_nrb(digits):
            found.append(("NRB", match.start(), match.end()))
        elif len(digits) == 11 and is_valid_pesel(digits):
            found.append(("PESEL", match.start(), match.end()))
        elif len(digits) == 10 and is_valid_nip(digits):
            found.append(("NIP", match.start(), match.end()))
        elif len(digits) in (9, 14) and is_valid_regon(digits):
            found.append(("REGON", match.start(), match.end()))
    return found
