"""Chunk 3.2 (``ml``): Presidio + spaCy ``pl_core_news_lg`` on Polish text (PLAN Etap 3)."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from spoldzielnia_ai.privacy.pl_identifiers import find_checksum_identifiers
from spoldzielnia_ai.privacy.scrubber import PiiScrubber
from tests.fixtures.pl_ids import make_iban_pl, make_nip, make_pesel, make_regon9, spaced

pytestmark = pytest.mark.ml


@pytest.fixture(scope="module")
def scrubber() -> PiiScrubber:
    try:
        from spoldzielnia_ai.privacy.presidio_adapter import PresidioAnalyzer

        return PiiScrubber(PresidioAnalyzer.shared())
    except (ImportError, OSError) as exc:  # model wheel not installed
        pytest.skip(f"pl_core_news_lg unavailable: {exc}")


def test_sample_note_is_fully_scrubbed(scrubber: PiiScrubber) -> None:
    pesel, nip, iban = make_pesel(dt.date(1968, 3, 14)), make_nip(), make_iban_pl()
    text = (
        f"Pasiekę prowadzi Jan Kowalski, PESEL {pesel}, zamieszkały przy ul. Lipowej 12/3, "
        f"05-800 Pruszków. NIP gospodarstwa: {nip[:3]}-{nip[3:6]}-{nip[6:8]}-{nip[8:]}, "
        f"konto {spaced(iban)}, tel. 600 700 800, e-mail jan.kowalski@example.org."
    )
    result = scrubber.scrub(text)
    for secret in (pesel, nip[:3], "Kowalski", "Lipowej", "Pruszków", "600 700 800", "example.org"):
        assert secret not in result.text, (secret, result.text)
    assert iban[4:8] not in result.text
    assert find_checksum_identifiers(result.text) == []
    assert "<OSOBA_1>" in result.text
    assert "<ADRES>" in result.text


def test_invalid_checksum_is_not_flagged(scrubber: PiiScrubber) -> None:
    """Precision: an 11-digit number with a wrong PESEL checksum stays."""
    text = "Numer partii miodu 44051401358 z ula nr 7."
    assert "44051401358" in scrubber.scrub(text).text


def test_knowledge_is_kept(scrubber: PiiScrubber) -> None:
    text = (
        "Polski Związek Pszczelarski zaleca leczenie warrozy kwasem szczawiowym zimą. "
        "Nad Wisłą i na Mazowszu sezon zaczyna się w kwietniu."
    )
    result = scrubber.scrub(text)
    for kept in ("Polski Związek Pszczelarski", "warrozy", "Wisłą", "Mazowszu", "kwietniu"):
        assert kept in result.text


def test_checksum_identifier_recall_on_corpus(scrubber: PiiScrubber) -> None:
    """≥ 0.95 recall for checksum-validated ids in varied Polish sentences."""
    rng = random.Random(2026)
    templates = [
        "Mój PESEL to {pesel}.",
        "PESEL: {pesel}, NIP: {nip}",
        "Proszę o przelew na rachunek {iban}.",
        "Firma (NIP {nip}, REGON {regon}) dostarczyła ramki.",
        "Zgłoszenie od osoby o numerze PESEL {pesel} dotyczy pasieki.",
        "Dane do faktury: NIP PL{nip_plain}.",
        "Konto: {iban}, tytułem: składka.",
        "REGON {regon} — spółdzielnia pszczelarska.",
    ]
    expected = found = 0
    for _ in range(40):
        pesel = make_pesel(
            dt.date(rng.randint(1930, 2010), rng.randint(1, 12), rng.randint(1, 28)),
            rng.randint(0, 999),
        )
        nip = make_nip(rng)
        values = {
            "pesel": pesel,
            "nip": f"{nip[:3]}-{nip[3:6]}-{nip[6:8]}-{nip[8:]}",
            "nip_plain": nip,
            "iban": spaced(make_iban_pl(rng)),
            "regon": make_regon9(rng),
        }
        for template in templates:
            text = template.format(**values)
            before = len(find_checksum_identifiers(text))
            after = len(find_checksum_identifiers(scrubber.scrub(text).text))
            expected += before
            found += before - after
    assert expected > 300
    assert found / expected >= 0.95, f"recall {found / expected:.3f}"


def test_pseudonyms_are_shared_across_promotion_sources(scrubber: PiiScrubber) -> None:
    """One person keeps one pseudonym across a promotion's sources (3.5)."""
    from spoldzielnia_ai.privacy.presidio_adapter import PresidioAnalyzer
    from spoldzielnia_ai.privacy.scrubber import ChecksumAnalyzer, CombinedAnalyzer

    combined = PiiScrubber(CombinedAnalyzer(PresidioAnalyzer.shared(), ChecksumAnalyzer()))
    first, second = combined.scrub_many(
        [
            "Jan Kowalski przeglądał ule w Zalesiu.",
            "Według Jana Kowalskiego rodziny są silne. Pomagała Anna Nowak.",
        ]
    )
    assert "<OSOBA_1>" in first.text
    assert "<OSOBA_1>" in second.text
    assert "<OSOBA_2>" in second.text
    assert "Kowalsk" not in first.text + second.text
