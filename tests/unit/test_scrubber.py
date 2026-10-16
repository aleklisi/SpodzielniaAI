"""Chunk 3.2: scrubbing logic with a fake analyzer (no Presidio)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from spoldzielnia_ai.privacy.scrubber import PiiFinding, PiiScrubber, ScrubPolicy


@dataclass
class FakeAnalyzer:
    """Finds the given (entity, regex, score) rules: deterministic stand-in for Presidio."""

    rules: list[tuple[str, str, float]]

    def analyze(self, text: str) -> list[PiiFinding]:
        return [
            PiiFinding(entity, m.start(), m.end(), score)
            for entity, pattern, score in self.rules
            for m in re.finditer(pattern, text)
        ]


def _scrub(text: str, *rules: tuple[str, str, float], policy: ScrubPolicy | None = None) -> str:
    scrubber = PiiScrubber(FakeAnalyzer(list(rules)))
    return scrubber.scrub(text, policy=policy or ScrubPolicy()).text


def test_identifiers_become_type_tags() -> None:
    text = "PESEL 44051401359, NIP 526-018-15-96."
    out = _scrub(text, ("PL_PESEL", r"\d{11}", 1.0), ("PL_NIP", r"\d{3}-\d{3}-\d{2}-\d{2}", 1.0))
    assert out == "PESEL <PESEL>, NIP <NIP>."


def test_person_pseudonyms_are_stable_across_inflection() -> None:
    text = "Jan Kowalski pisze. Jana Kowalskiego nie było. Kowalski wrócił. Anna Nowak też."
    out = _scrub(
        text,
        ("PERSON", r"Jana? Kowalski(?:ego)?|(?<= )Kowalski(?= wrócił)|Anna Nowak", 0.85),
    )
    assert out == "<OSOBA_1> pisze. <OSOBA_1> nie było. <OSOBA_1> wrócił. <OSOBA_2> też."


def test_places_are_numbered_separately_from_people() -> None:
    text = "Kraków i Kraków, potem Tarnów; Jan."
    out = _scrub(text, ("LOCATION", r"Kraków|Tarnów", 0.85), ("PERSON", r"Jan", 0.85))
    assert out == "<MIEJSCE_1> i <MIEJSCE_1>, potem <MIEJSCE_2>; <OSOBA_1>."


def test_overlaps_go_to_the_stronger_finding() -> None:
    text = "jan.kowalski@example.org"
    out = _scrub(
        text,
        ("EMAIL_ADDRESS", r".+", 1.0),
        ("PERSON", r"jan\.kowalski", 0.85),
    )
    assert out == "<EMAIL>"


def test_thresholds_and_policy() -> None:
    text = "KRS 0000123456 i WNI 14650123, organizacja PZP."
    rules = (
        ("PL_KRS", r"KRS \d{10}", 1.0),
        ("PL_WNI", r"14650123", 0.3),
        ("ORGANIZATION", r"PZP", 0.85),
    )
    assert _scrub(text, *rules) == "<KRS> i WNI 14650123, organizacja PZP."  # WNI below 0.5
    strict = ScrubPolicy(thresholds={"PL_WNI": 0.2})
    assert _scrub(text, *rules, policy=strict) == "<KRS> i WNI <WNI>, organizacja PZP."


def test_findings_hold_no_values() -> None:
    scrubber = PiiScrubber(FakeAnalyzer([("PL_PESEL", r"\d{11}", 1.0)]))
    result = scrubber.scrub("PESEL 44051401359")
    (finding,) = result.findings
    assert (finding.entity, finding.start, finding.end) == ("PL_PESEL", 6, 17)
    assert "44051401359" not in repr(result.findings)
    assert result.stats == {"PL_PESEL": 1}
    assert result.changed


def test_unchanged_text() -> None:
    result = PiiScrubber(FakeAnalyzer([])).scrub("Miód rzepakowy krystalizuje szybko.")
    assert result.text == "Miód rzepakowy krystalizuje szybko."
    assert not result.changed


@pytest.mark.parametrize("bad", [PiiFinding("PERSON", 5, 5, 0.9), PiiFinding("UNKNOWN", 0, 3, 1.0)])
def test_degenerate_and_unknown_findings_are_ignored(bad: PiiFinding) -> None:
    class One:
        def analyze(self, text: str) -> list[PiiFinding]:
            return [bad]

    assert PiiScrubber(One()).scrub("abcdefgh").text == "abcdefgh"
