"""The UI palette derived from an organisation's two colours (chunk 13.1, US-07, rule R1).

The administrator picks a **brand** and an **accent** colour. Every other shade the UI needs is
derived here, in OKLCH (perceptual lightness, so steps look even), keeping each colour's hue and
moving only its lightness until WCAG 2.2 AA holds against the fixed surfaces:

- light ``primary``: white button text on it at 4.5:1; ``primary-strong`` (links, active menu
  text, hover) a step darker and readable (4.5:1) on every surface and on the 15 % tints that
  badges use; ``accent`` readable the same way;
- dark mode: the same rules against the dark surfaces, with a near-black ink on ``primary``;
- the menu background (``sidebar``) is a faint tint of the brand that keeps the menu's text at
  4.5:1.

Background, text, border and status colours stay fixed (``frontend/src/app/globals.css``), so
their meaning never changes. If no lightness of a hue can reach a ratio, the colour is refused
(``ContrastUnreachable``). The output holds only ``#rrggbb`` strings: it is what reaches the
page's CSS (rule R2), so nothing else may.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

HEX = re.compile(r"^#[0-9a-f]{6}$")

Mode = Literal["light", "dark"]
Token = Literal[
    "primary", "primary-strong", "primary-contrast", "accent", "sidebar", "sidebar-accent"
]
TOKENS: tuple[Token, ...] = (
    "primary",
    "primary-strong",
    "primary-contrast",
    "accent",
    "sidebar",
    "sidebar-accent",
)

# The fixed surfaces and text of globals.css (keep in sync).
SURFACES: dict[Mode, dict[str, str]] = {
    "light": {
        "bg": "#fbf8f1",
        "surface": "#ffffff",
        "surface-muted": "#f3eee2",
        "text": "#241f16",
        "text-muted": "#6b6354",
    },
    "dark": {
        "bg": "#15130f",
        "surface": "#1f1c16",
        "surface-muted": "#29251d",
        "text": "#f2ede2",
        "text-muted": "#b3aa98",
    },
}

TEXT_RATIO = 4.5  # WCAG 1.4.3, normal text
UI_RATIO = 3.0  # WCAG 1.4.11, the boundary of a control
TINT = 0.15  # badges and highlights: `bg-<colour>/15` behind `text-<colour>`
STEP = 0.005  # lightness search step (OKLCH L in 0…1)

# The honey palette of globals.css, the look without any branding.
DEFAULT_BRAND = "#9a6414"
DEFAULT_ACCENT = "#6b4f96"


class ContrastUnreachable(ValueError):
    """No lightness of this hue gives readable text: the colour is refused."""

    def __init__(self, which: str, mode: Mode) -> None:
        super().__init__(f"{which}: contrast unreachable in {mode} mode")
        self.which = which
        self.mode = mode


@dataclass(frozen=True, slots=True)
class Check:
    """One contrast requirement, as shown to the administrator in the preview."""

    mode: Mode
    token: Token
    against: str  # what it is measured against (a surface or a token)
    ratio: float
    required: float


@dataclass(frozen=True, slots=True)
class Palette:
    light: dict[Token, str]
    dark: dict[Token, str]
    adjusted: list[Literal["brand", "accent"]] = field(
        default_factory=list[Literal["brand", "accent"]]
    )
    checks: list[Check] = field(default_factory=list[Check])

    def tokens(self) -> dict[str, dict[str, str]]:
        """What is stored and sent to the page: only the token names and their hex values."""
        return {
            "light": {k: v for k, v in self.light.items()},
            "dark": {k: v for k, v in self.dark.items()},
        }


# --- Colour arithmetic ----------------------------------------------------------------------


def normalize_hex(value: str) -> str:
    """``#RRGGBB`` or ``#rgb`` (any case) as ``#rrggbb``; anything else is a ``ValueError``."""
    text = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if not HEX.fullmatch(text):
        raise ValueError("not a #rrggbb colour")
    return text


def _rgb(hex_: str) -> tuple[float, float, float]:
    return (int(hex_[1:3], 16) / 255, int(hex_[3:5], 16) / 255, int(hex_[5:7], 16) / 255)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def _linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def luminance(hex_: str) -> float:
    r, g, b = (_linear(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio, 1…21."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def blend(fg: str, bg: str, alpha: float) -> str:
    """``fg`` at ``alpha`` over ``bg``, as the browser composites `bg-x/15` (in sRGB)."""
    f, b = _rgb(fg), _rgb(bg)
    return _hex(
        (
            f[0] * alpha + b[0] * (1 - alpha),
            f[1] * alpha + b[1] * (1 - alpha),
            f[2] * alpha + b[2] * (1 - alpha),
        )
    )


@dataclass(frozen=True, slots=True)
class Oklch:
    lightness: float
    chroma: float
    hue: float  # radians


def to_oklch(hex_: str) -> Oklch:
    r, g, b = (_linear(c) for c in _rgb(hex_))
    l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return Oklch(lightness, math.hypot(a, bb), math.atan2(bb, a))


def _oklch_rgb(color: Oklch) -> tuple[float, float, float]:
    a = color.chroma * math.cos(color.hue)
    b = color.chroma * math.sin(color.hue)
    l_ = color.lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = color.lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = color.lightness - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_**3, m_**3, s_**3
    return (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )


def from_oklch(color: Oklch) -> str:
    """The nearest sRGB colour: chroma is reduced (hue and lightness kept) until it fits."""
    low, high = 0.0, color.chroma
    candidate = Oklch(color.lightness, high, color.hue)
    if not _in_gamut(_oklch_rgb(candidate)):
        for _ in range(24):
            mid = (low + high) / 2
            if _in_gamut(_oklch_rgb(Oklch(color.lightness, mid, color.hue))):
                low = mid
            else:
                high = mid
        candidate = Oklch(color.lightness, low, color.hue)
    return _hex(tuple(_gamma(max(0.0, c)) for c in _oklch_rgb(candidate)))  # pyright: ignore[reportArgumentType]


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-4 <= c <= 1 + 1e-4 for c in rgb)


def with_lightness(hex_: str, lightness: float, max_chroma: float | None = None) -> str:
    color = to_oklch(hex_)
    chroma = color.chroma if max_chroma is None else min(color.chroma, max_chroma)
    return from_oklch(Oklch(min(1.0, max(0.0, lightness)), chroma, color.hue))


def search(
    hex_: str,
    ok: Callable[[str], bool],
    direction: Literal["darker", "lighter"],
    *,
    which: str,
    mode: Mode,
    start: float | None = None,
    max_chroma: float | None = None,
) -> str:
    """The first colour of ``hex_``'s hue, from ``start`` (its own lightness) towards black or
    white, that satisfies ``ok``. The colour itself when it already does."""
    lightness = to_oklch(hex_).lightness if start is None else start
    first = with_lightness(hex_, lightness, max_chroma) if start is not None or max_chroma else hex_
    if ok(first):
        return first
    sign = -1 if direction == "darker" else 1
    steps = int(1 / STEP) + 1
    for i in range(1, steps + 1):
        target = lightness + sign * i * STEP
        if not 0.0 <= target <= 1.0:
            break
        candidate = with_lightness(hex_, target, max_chroma)
        if ok(candidate):
            return candidate
    raise ContrastUnreachable(which, mode)


# --- Derivation ------------------------------------------------------------------------------


def _readable(color: str, mode: Mode) -> bool:
    """Text in ``color`` reads at 4.5:1 on every surface and on its own 15 % tint over them."""
    surfaces = SURFACES[mode]
    grounds = [surfaces["bg"], surfaces["surface"], surfaces["surface-muted"]]
    grounds += [blend(color, g, TINT) for g in grounds]
    return all(contrast(color, g) >= TEXT_RATIO for g in grounds)


def _menu_readable(color: str, mode: Mode) -> bool:
    surfaces = SURFACES[mode]
    return (
        contrast(surfaces["text-muted"], color) >= TEXT_RATIO
        and contrast(surfaces["text"], color) >= TEXT_RATIO
    )


WHITE = "#ffffff"
LIGHT_STEP = 0.08  # primary-strong is this much darker (light) or lighter (dark) than primary


def derive(brand: str, accent: str) -> Palette:
    """The light and dark tokens for ``brand`` and ``accent`` (both ``#rrggbb``)."""
    brand, accent = normalize_hex(brand), normalize_hex(accent)
    light: dict[Token, str] = {}
    dark: dict[Token, str] = {}

    # Light: white text on the brand; a darker strong step readable everywhere.
    light["primary-contrast"] = WHITE
    light["primary"] = search(
        brand, lambda c: contrast(WHITE, c) >= TEXT_RATIO, "darker", which="brand", mode="light"
    )
    strong_start = to_oklch(light["primary"]).lightness - LIGHT_STEP
    light["primary-strong"] = search(
        light["primary"],
        lambda c: _readable(c, "light") and contrast(WHITE, c) >= TEXT_RATIO,
        "darker",
        which="brand",
        mode="light",
        start=strong_start,
    )
    light["accent"] = search(
        accent, lambda c: _readable(c, "light"), "darker", which="accent", mode="light"
    )
    light["sidebar"] = search(
        brand,
        lambda c: _menu_readable(c, "light"),
        "lighter",
        which="brand",
        mode="light",
        start=0.965,
        max_chroma=0.02,
    )
    light["sidebar-accent"] = search(
        brand,
        lambda c: _menu_readable(c, "light"),
        "lighter",
        which="brand",
        mode="light",
        start=0.925,
        max_chroma=0.035,
    )

    # Dark: a near-black ink of the brand's hue on a lighter brand.
    ink = with_lightness(brand, 0.2, 0.04)
    dark_bg = SURFACES["dark"]["bg"]
    dark["primary-contrast"] = ink
    dark["primary"] = search(
        brand,
        lambda c: contrast(ink, c) >= TEXT_RATIO and contrast(c, dark_bg) >= UI_RATIO,
        "lighter",
        which="brand",
        mode="dark",
    )
    dark["primary-strong"] = search(
        dark["primary"],
        lambda c: _readable(c, "dark") and contrast(ink, c) >= TEXT_RATIO,
        "lighter",
        which="brand",
        mode="dark",
        start=min(1.0, to_oklch(dark["primary"]).lightness + LIGHT_STEP),
    )
    dark["accent"] = search(
        accent, lambda c: _readable(c, "dark"), "lighter", which="accent", mode="dark"
    )
    dark["sidebar"] = search(
        brand,
        lambda c: _menu_readable(c, "dark"),
        "darker",
        which="brand",
        mode="dark",
        start=0.19,
        max_chroma=0.012,
    )
    dark["sidebar-accent"] = search(
        brand,
        lambda c: _menu_readable(c, "dark"),
        "darker",
        which="brand",
        mode="dark",
        start=0.27,
        max_chroma=0.02,
    )

    adjusted: list[Literal["brand", "accent"]] = []
    if light["primary"] != brand:
        adjusted.append("brand")
    if light["accent"] != accent:
        adjusted.append("accent")
    return Palette(light, dark, adjusted, _checks(light, dark))


def _checks(light: dict[Token, str], dark: dict[Token, str]) -> list[Check]:
    checks: list[Check] = []
    pairs: tuple[tuple[Mode, dict[Token, str]], ...] = (("light", light), ("dark", dark))
    for mode, tokens in pairs:
        surfaces = SURFACES[mode]
        checks.append(
            Check(
                mode,
                "primary-contrast",
                "primary",
                round(contrast(tokens["primary-contrast"], tokens["primary"]), 2),
                TEXT_RATIO,
            )
        )
        checks.append(
            Check(
                mode,
                "primary",
                "bg",
                round(contrast(tokens["primary"], surfaces["bg"]), 2),
                UI_RATIO,
            )
        )
        for token in ("primary-strong", "accent"):
            worst = min(
                contrast(tokens[token], g)
                for base in (surfaces["bg"], surfaces["surface"], surfaces["surface-muted"])
                for g in (base, blend(tokens[token], base, TINT))
            )
            checks.append(Check(mode, token, "surfaces", round(worst, 2), TEXT_RATIO))
        checks.append(
            Check(
                mode,
                "sidebar-accent",
                "text-muted",
                round(contrast(surfaces["text-muted"], tokens["sidebar-accent"]), 2),
                TEXT_RATIO,
            )
        )
    return checks


def is_valid_tokens(value: object) -> bool:
    """A stored palette: exactly the two modes, known tokens only, every value ``#rrggbb``."""
    if not isinstance(value, dict) or set(value) != {"light", "dark"}:  # pyright: ignore[reportUnknownArgumentType]
        return False
    for mode in ("light", "dark"):
        tokens = value[mode]  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(tokens, dict) or set(tokens) != set(TOKENS):  # pyright: ignore[reportUnknownArgumentType]
            return False
        if not all(isinstance(v, str) and HEX.fullmatch(v) for v in tokens.values()):  # pyright: ignore[reportUnknownVariableType]
            return False
    return True
