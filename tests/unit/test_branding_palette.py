"""Chunk 13.1: the palette derived from an organisation's brand and accent colours (R1)."""

from __future__ import annotations

import pytest

from spoldzielnia_ai.branding.palette import (
    DEFAULT_ACCENT,
    DEFAULT_BRAND,
    HEX,
    SURFACES,
    TEXT_RATIO,
    TINT,
    TOKENS,
    UI_RATIO,
    ContrastUnreachable,
    Mode,
    Token,
    blend,
    contrast,
    derive,
    from_oklch,
    is_valid_tokens,
    normalize_hex,
    search,
    to_oklch,
)

SAMPLES = [
    (DEFAULT_BRAND, DEFAULT_ACCENT),
    ("#1f6f4a", "#c2410c"),  # forest and rust
    ("#fff59d", "#00ffff"),  # far too light for text
    ("#000000", "#ffffff"),
    ("#808080", "#808080"),
    ("#ff0000", "#0000ff"),
    ("#0b3d91", "#f59e0b"),
]


def test_contrast_matches_wcag_reference_values() -> None:
    assert contrast("#ffffff", "#000000") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0)


@pytest.mark.parametrize(
    "value", ["#9a6414", "#1f6f4a", "#ffffff", "#000000", "#c2410c", "#0000ff"]
)
def test_oklch_round_trip(value: str) -> None:
    assert from_oklch(to_oklch(value)) == value


@pytest.mark.parametrize(
    ("raw", "expected"), [("#ABCDEF", "#abcdef"), (" #abc ", "#aabbcc"), ("#1F6F4A", "#1f6f4a")]
)
def test_normalize_hex(raw: str, expected: str) -> None:
    assert normalize_hex(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["red", "#12345", "#1234567", "1f6f4a", "#gggggg", "#1f6f4a;}body{display:none", "url(x)", ""],
)
def test_anything_but_a_hex_colour_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="#rrggbb"):
        normalize_hex(raw)


def test_the_default_honey_palette_is_kept_as_it_is() -> None:
    palette = derive(DEFAULT_BRAND, DEFAULT_ACCENT)
    assert palette.adjusted == []
    assert palette.light["primary"] == DEFAULT_BRAND
    assert palette.light["accent"] == DEFAULT_ACCENT


def test_a_light_yellow_is_darkened_until_white_text_reads_on_it() -> None:
    palette = derive("#fff59d", DEFAULT_ACCENT)
    assert palette.adjusted == ["brand"]
    assert contrast("#ffffff", palette.light["primary"]) >= TEXT_RATIO
    # Same hue, only darker.
    assert abs(to_oklch(palette.light["primary"]).hue - to_oklch("#fff59d").hue) < 0.05
    assert to_oklch(palette.light["primary"]).lightness < to_oklch("#fff59d").lightness
    # In dark mode the light yellow already works under dark ink.
    assert palette.dark["primary"] == "#fff59d"


@pytest.mark.parametrize(("brand", "accent"), SAMPLES)
def test_every_derived_pair_meets_its_ratio_in_both_modes(brand: str, accent: str) -> None:
    palette = derive(brand, accent)
    modes: tuple[tuple[Mode, dict[Token, str]], ...] = (
        ("light", palette.light),
        ("dark", palette.dark),
    )
    for mode, tokens in modes:
        surfaces = SURFACES[mode]
        assert contrast(tokens["primary-contrast"], tokens["primary"]) >= TEXT_RATIO
        assert contrast(tokens["primary"], surfaces["bg"]) >= UI_RATIO
        for token in ("primary-strong", "accent"):
            for base in (surfaces["bg"], surfaces["surface"], surfaces["surface-muted"]):
                assert contrast(tokens[token], base) >= TEXT_RATIO, (mode, token, base)
                assert contrast(tokens[token], blend(tokens[token], base, TINT)) >= TEXT_RATIO
        for ground in (tokens["sidebar"], tokens["sidebar-accent"]):
            assert contrast(surfaces["text-muted"], ground) >= TEXT_RATIO
    assert all(check.ratio >= check.required for check in palette.checks)


@pytest.mark.parametrize(("brand", "accent"), SAMPLES)
def test_the_palette_holds_only_known_tokens_with_hex_values(brand: str, accent: str) -> None:
    tokens = derive(brand, accent).tokens()
    assert is_valid_tokens(tokens)
    for mode in ("light", "dark"):
        assert tuple(sorted(tokens[mode])) == tuple(sorted(TOKENS))
        assert all(HEX.fullmatch(v) for v in tokens[mode].values())


def test_primary_strong_is_a_step_beyond_primary() -> None:
    palette = derive("#1f6f4a", "#c2410c")
    assert (
        to_oklch(palette.light["primary-strong"]).lightness
        < to_oklch(palette.light["primary"]).lightness
    )
    assert (
        to_oklch(palette.dark["primary-strong"]).lightness
        > to_oklch(palette.dark["primary"]).lightness
    )


def test_a_requirement_no_lightness_can_meet_is_refused() -> None:
    with pytest.raises(ContrastUnreachable) as caught:
        search("#808080", lambda _c: False, "darker", which="brand", mode="light")
    assert caught.value.which == "brand"


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"light": {}},
        {"light": dict.fromkeys(TOKENS, "#ffffff"), "dark": dict.fromkeys(TOKENS, "red")},
        {
            "light": dict.fromkeys(TOKENS, "#ffffff"),
            "dark": {**dict.fromkeys(TOKENS, "#000000"), "evil": "#000000"},
        },
        {"light": dict.fromkeys(TOKENS, "#ffffff;}"), "dark": dict.fromkeys(TOKENS, "#000000")},
    ],
)
def test_a_tampered_palette_is_not_valid(value: object) -> None:
    assert not is_valid_tokens(value)
