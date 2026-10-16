"""The only module touching Presidio (untyped, conventions §2.2): Polish ``Analyzer``.

- NLP: spaCy ``pl_core_news_lg`` (installed from its locked wheel, offline). NER labels:
  ``persName`` → PERSON, ``placeName`` → LOCATION (settlements: a village name can locate an
  apiary), ``orgName`` → ORGANIZATION (kept by the default policy). ``geogName`` (rivers,
  regions) is not mapped: regional knowledge is not personal data.
- Pattern recognizers wrap the checksum validators of ``pl_identifiers``. A failed checksum
  drops the candidate, a passed one scores 1.0. Context-only formats (KRS, WNI, bare NRB, postal
  codes) start low and need Polish context words to reach their threshold.
- Built-ins in Polish: e-mail, IP, credit card, phone (``phonenumbers`` with region PL).

The engine is heavy (≈1 GB model), so ``PresidioAnalyzer.shared()`` builds one per process.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, ClassVar

from presidio_analyzer import (  # pyright: ignore[reportMissingTypeStubs]
    AnalyzerEngine,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider  # pyright: ignore[reportMissingTypeStubs]
from presidio_analyzer.predefined_recognizers import (  # pyright: ignore[reportMissingTypeStubs]
    CreditCardRecognizer,
    EmailRecognizer,
    IpRecognizer,
    PhoneRecognizer,
    SpacyRecognizer,
)

from spoldzielnia_ai.privacy import pl_identifiers as ids
from spoldzielnia_ai.privacy.scrubber import PiiFinding

if TYPE_CHECKING:
    from collections.abc import Callable

LANGUAGE = "pl"
SPACY_MODEL = "pl_core_news_lg"

ADDRESS_PATTERN = (
    r"(?<!\w)(?:ul\.|ulica|ulicy|al\.|aleja|alei|os\.|osiedle|pl\.|plac)\s+"
    r"(?:[A-ZĄĆĘŁŃÓŚŹŻ0-9][\wąćęłńóśźż.\-]*\s+){0,4}\d{1,4}[a-zA-Z]?(?:\s*/\s*\d{1,4})?"
    r"(?:\s*(?:m\.|lok\.)\s*\d{1,4})?"
)
POSTAL_CODE_PATTERN = r"(?<!\d)\d{2}-\d{3}(?!\d)"
PHONE_PATTERNS = (
    (r"(?<!\d)\+48[ \-]?\d{3}[ \-]?\d{3}[ \-]?\d{3}(?!\d)", 0.7),
    (r"(?<![\d\-])\d{3}[ \-]\d{3}[ \-]\d{3}(?![\d\-])", 0.55),
    (r"(?<![\d\-])\(?\d{2}\)?[ \-]\d{3}[ \-]\d{2}[ \-]\d{2}(?![\d\-])", 0.55),
)


class _ValidatedRecognizer(PatternRecognizer):  # pyright: ignore[reportUntypedBaseClass]
    """A pattern recognizer whose candidates must pass ``validator`` (None: no opinion)."""

    def __init__(
        self,
        entity: str,
        patterns: list[tuple[str, float]],
        validator: Callable[[str], bool | None] | None,
        context: list[str] | None = None,
    ) -> None:
        self._validator = validator
        super().__init__(
            supported_entity=entity,
            name=f"Pl{entity.title().replace('_', '')}Recognizer",
            supported_language=LANGUAGE,
            patterns=[
                Pattern(f"{entity}_{i}", regex, score) for i, (regex, score) in enumerate(patterns)
            ],
            context=context or [],
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        if self._validator is None:
            return None
        return self._validator(pattern_text)


def _recognizers() -> list[Any]:
    def krs(text: str) -> bool | None:
        """``KRS 0000123456``: sure (True). A bare 10-digit run: reject if malformed,
        otherwise abstain (None), so only context words can make it KRS."""
        valid = ids.is_valid_krs(text)
        if not text[:1].isdigit():
            return valid
        return None if valid else False

    return [
        _ValidatedRecognizer("PL_PESEL", [(ids.PESEL_PATTERN, 0.4)], ids.is_valid_pesel, ["pesel"]),
        _ValidatedRecognizer(
            "PL_NIP", [(p, 0.4) for p in ids.NIP_PATTERNS], ids.is_valid_nip, ["nip"]
        ),
        _ValidatedRecognizer("PL_REGON", [(ids.REGON_PATTERN, 0.3)], ids.is_valid_regon, ["regon"]),
        _ValidatedRecognizer(
            "PL_KRS",
            [(r"(?i)(?<!\w)KRS[:\s]*(?:nr\.?\s*)?\d{10}(?!\d)", 0.85), (ids.KRS_PATTERN, 0.1)],
            krs,
            list(ids.KRS_CONTEXT),
        ),
        _ValidatedRecognizer(
            "IBAN_CODE",
            [(ids.IBAN_PL_PATTERN, 0.5), (ids.IBAN_PATTERN, 0.4)],
            ids.is_valid_iban,
            ["iban", "konto", "rachunek"],
        ),
        _ValidatedRecognizer(
            "PL_NRB", [(ids.NRB_PATTERN, 0.3)], ids.is_valid_nrb, list(ids.NRB_CONTEXT)
        ),
        _ValidatedRecognizer(
            "PL_PARCEL",
            [(ids.PARCEL_TERYT_PATTERN, 0.5), (ids.PARCEL_SHORT_PATTERN, 0.6)],
            lambda text: ids.is_valid_parcel_id(text) if "_" in text else True,
            list(ids.PARCEL_CONTEXT),
        ),
        _ValidatedRecognizer("PL_WNI", [(ids.WNI_PATTERN, 0.1)], None, list(ids.WNI_CONTEXT)),
        _ValidatedRecognizer("PL_ADDRESS", [(ADDRESS_PATTERN, 0.7)], None),
        _ValidatedRecognizer(
            "PL_POSTAL_CODE",
            [(POSTAL_CODE_PATTERN, 0.3)],
            None,
            ["kod", "pocztowy", "poczta", "adres", "ul", "ulica", "miejscowość"],
        ),
        _ValidatedRecognizer(
            "PHONE_NUMBER",
            list(PHONE_PATTERNS),
            None,
            ["tel", "telefon", "telefonu", "kom", "komórka", "komórkowy", "numer", "dzwonić"],
        ),
        PhoneRecognizer(
            supported_language=LANGUAGE,
            supported_regions=("PL",),
            context=["tel", "telefon", "telefonu", "kom", "komórka"],
        ),
        EmailRecognizer(supported_language=LANGUAGE),
        IpRecognizer(supported_language=LANGUAGE),
        CreditCardRecognizer(supported_language=LANGUAGE, context=["karta", "karty", "kredytowa"]),
        SpacyRecognizer(
            supported_language=LANGUAGE, supported_entities=["PERSON", "LOCATION", "ORGANIZATION"]
        ),
    ]


class PresidioAnalyzer:
    """``Analyzer`` (scrubber protocol) backed by Presidio + spaCy Polish."""

    _shared: ClassVar[PresidioAnalyzer | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": LANGUAGE, "model_name": SPACY_MODEL}],
                "ner_model_configuration": {
                    "model_to_presidio_entity_mapping": {
                        "persName": "PERSON",
                        "placeName": "LOCATION",
                        "orgName": "ORGANIZATION",
                    },
                    "labels_to_ignore": ["geogName", "date", "time"],
                },
            }
        )
        registry = RecognizerRegistry(supported_languages=[LANGUAGE])
        for recognizer in _recognizers():
            registry.add_recognizer(recognizer)
        self._engine: Any = AnalyzerEngine(
            registry=registry,
            nlp_engine=provider.create_engine(),
            supported_languages=[LANGUAGE],
        )

    @classmethod
    def shared(cls) -> PresidioAnalyzer:
        with cls._lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    def analyze(self, text: str) -> list[PiiFinding]:
        results: list[Any] = self._engine.analyze(text=text, language=LANGUAGE)
        return [
            PiiFinding(str(r.entity_type), int(r.start), int(r.end), float(r.score))
            for r in results
            if float(r.score) > 0
        ]
