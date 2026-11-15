"""Design tokens shared by the GTK and Tkinter frontends.

This module deliberately imports nothing from GTK: the Tkinter frontend, the
unit tests, and the GTK theme layer all read the same dictionaries, so the two
frontends cannot drift apart in colour.

The palette is the single source of truth.  ``theme_gtk`` turns it into
``@define-color`` lines for GTK's CSS parser; ``style.css`` only ever names
those tokens and never contains a literal colour.  That is what makes the
in-app Dark/Light choice independent of the desktop theme.
"""

from __future__ import annotations

import re
from typing import Mapping

#: ``system`` follows the desktop, the other two override it completely.
MODES = ("system", "dark", "light")
DEFAULT_MODE = "system"

#: The accent family, shared with the Windows frontend.
ACCENT = "#8b5cf6"
#: The accent for *filled* surfaces that carry white labels: white on #8b5cf6 is
#: only 3.7:1, while this shade is 5.6:1 and still reads as the same colour.
ACCENT_FILL = "#7c3aed"
ACCENT_FILL_HOVER = "#6d28d9"
#: The one warm colour kept from the previous design: the favourite star.
FAVORITE = "#f5c518"
#: Accent at 18% opacity, for selected pills and quiet highlights.  GTK's
#: ``@define-color`` cannot compute alpha() portably, so the value is spelled
#: out and kept in step with the accent above.
ACCENT_SOFT_DARK = "rgba(139, 92, 246, 0.18)"
ACCENT_SOFT_LIGHT = "rgba(124, 58, 237, 0.18)"

DARK: dict[str, str] = {
    "bg": "#0f1014",
    "surface": "#16181d",
    "raised": "#1d2027",
    "hover": "#262b34",
    "divider": "#2a2d36",
    "text": "#e8e9ee",
    "muted": "#a4a7b3",
    # The shared tokens lightened the contract's #7c8090 to #858998 so muted
    # text clears 4.5:1 on overlay as well; design_tokens.py is the source.
    "faint": "#858998",
    "accent": ACCENT,
    "accent_hover": "#9d74f8",
    "accent_soft": ACCENT_SOFT_DARK,
    "accent_fill": ACCENT_FILL,
    "accent_fill_hover": ACCENT_FILL_HOVER,
    #: Accent dark enough (or light enough) to read as *text*; the fill accent
    #: itself is only 3.7:1 on white, so links and inline accent text use this.
    "accent_text": "#a78bfa",
    "on_accent": "#ffffff",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    #: The same hues as *text*: the contract's warning/danger are tuned for
    #: fills, and light mode needs a deeper shade to clear 4.5:1 on the page.
    "warning_text": "#f59e0b",
    "danger_text": "#ef4444",
    "favorite": FAVORITE,
    "shadow": "rgba(0, 0, 0, 0.55)",
    #: The hero sits on a dark scrim in both modes, so this one never switches.
    "hero_text": "#f2f4f6",
}

LIGHT: dict[str, str] = {
    "bg": "#f4f5f8",
    "surface": "#ffffff",
    "raised": "#ffffff",
    "hover": "#eceef2",
    "divider": "#dcdfe6",
    "text": "#14161c",
    "muted": "#4a4f5c",
    "faint": "#6b7080",
    "accent": "#7c3aed",
    "accent_hover": "#6d28d9",
    "accent_soft": ACCENT_SOFT_LIGHT,
    "accent_fill": ACCENT_FILL,
    "accent_fill_hover": ACCENT_FILL_HOVER,
    "accent_text": "#6d28d9",
    "on_accent": "#ffffff",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "warning_text": "#b45309",
    "danger_text": "#b91c1c",
    "favorite": FAVORITE,
    "shadow": "rgba(15, 23, 42, 0.12)",
    "hero_text": "#f2f4f6",
}

PALETTES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

#: Tokens that carry text and must stay readable on ``bg`` and ``surface``.
TEXT_TOKENS = ("text", "muted")
#: Tokens that are label text on a filled background instead.
FILL_TEXT_TOKENS = ("on_accent",)

#: CSS name for each token.  The shared contract fixes these names: the palette
#: keeps its internal keys (``muted``/``faint``) while the CSS sees the contract
#: names (``text2``/``muted``), so a rule reads the same as the design brief.
CSS_NAMES: dict[str, str] = {
    "raised": "overlay",
    "divider": "border",
    "muted": "text2",
    "faint": "muted",
}

#: WCAG 2.1 AA for normal text, and AA for large text / UI components.
CONTRAST_TEXT = 4.5
CONTRAST_LARGE = 3.0

#: 8px spacing scale, then 48 for page gutters.
SPACE: dict[str, int] = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32, "xxl": 48}

#: Radii from the brief: cards/buttons, dialogs, pills.
RADIUS: dict[str, int] = {"sm": 6, "md": 8, "lg": 12, "pill": 999}

#: One font, four sizes.  Sizes are points for GTK, and the Tk port uses the
#: same numbers.
TYPE: dict[str, int] = {"page": 22, "title": 16, "body": 13, "small": 11}


class ThemeError(ValueError):
    """A palette or token name was not usable."""


def palette(resolved: str) -> Mapping[str, str]:
    """Return the palette for ``"dark"`` or ``"light"``.

    A bare ``"system"`` is not resolved here on purpose: only the caller knows
    whether the desktop is dark, and guessing would hide that decision.
    """
    try:
        return PALETTES[resolved]
    except KeyError:
        raise ThemeError(f"Unknown palette {resolved!r}; expected dark or light.") from None


def resolve(mode: str, *, system_dark: bool) -> str:
    """Return ``"dark"`` or ``"light"`` for a stored mode."""
    if mode == "dark":
        return "dark"
    if mode == "light":
        return "light"
    return "dark" if system_dark else "light"


def normalize_mode(mode: object) -> str:
    """Coerce a stored setting into one of :data:`MODES`."""
    text = str(mode or "").strip().lower()
    return text if text in MODES else DEFAULT_MODE


def is_dark_theme_name(name: object) -> bool | None:
    """Read a GTK theme name as dark, light, or *unknown*.

    GTK3 exposes ``gtk-application-prefer-dark-theme`` as an *application
    request*, not as the desktop's preference, and it is usually ``False`` even
    on a dark desktop.  The theme name is the reliable signal: ``adw-gtk3-dark``
    is dark, ``Adwaita`` says nothing either way.  ``None`` means "ask something
    else", never "assume light".
    """
    text = str(name or "").strip().lower()
    if not text:
        return None
    if "dark" in text:
        return True
    if "light" in text:
        return False
    return None


def css_name(token: str) -> str:
    """CSS name (without the ``@``) for a palette token."""
    if token not in DARK:
        raise ThemeError(f"Unknown theme token {token!r}.")
    return CSS_NAMES.get(token, token)


def css_variables(resolved: str, *, accent: str | None = None) -> str:
    """Render a palette as GTK ``@define-color`` declarations.

    ``accent`` retints the accent family for the session, so the same generated
    sheet serves every accent; a value that is not a colour is ignored.
    """
    colors = palette(resolved)
    if accent:
        colors = with_accent(colors, accent)
    lines = [
        "/* Generated from lgl/theme.py — do not edit by hand. */",
        "/* Palette: " + resolved + " */",
    ]
    for token in colors:
        lines.append(f"@define-color {css_name(token)} {colors[token]};")
    return "\n".join(lines) + "\n"


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGBA = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([0-9.]+)\s*)?\)$"
)


def _channels(value: str) -> tuple[float, float, float]:
    """Parse ``#rgb``/``#rrggbb``/``rgb()``/``rgba()`` into 0-255 channels."""
    text = str(value).strip()
    if _HEX.match(text):
        body = text[1:]
        if len(body) in (3, 4):
            body = "".join(char * 2 for char in body[:3])
        return tuple(float(int(body[i : i + 2], 16)) for i in (0, 2, 4))  # type: ignore[return-value]
    match = _RGBA.match(text)
    if match:
        return tuple(float(int(match.group(i))) for i in (1, 2, 3))  # type: ignore[return-value]
    raise ThemeError(f"Not a colour: {value!r}")


def relative_luminance(value: str) -> float:
    """WCAG relative luminance of a colour string."""
    channels = []
    for channel in _channels(value):
        scaled = channel / 255.0
        channels.append(
            scaled / 12.92
            if scaled <= 0.04045
            else ((scaled + 0.055) / 1.055) ** 2.4
        )
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two colours (1.0 – 21.0)."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _mix(first: str, second: str, amount: float) -> str:
    """Blend ``amount`` (0-1) of ``second`` into ``first``."""
    try:
        left = _channels(first)
        right = _channels(second)
    except ThemeError:
        return first
    blended = [
        max(0, min(255, round(a + (b - a) * amount)))
        for a, b in zip(left, right)
    ]
    return "#" + "".join(f"{value:02x}" for value in blended)


def _reads_on(foreground: str, background: str) -> bool:
    try:
        return contrast_ratio(foreground, background) >= CONTRAST_TEXT
    except ThemeError:
        return True


def _is_dark(value: str) -> bool:
    try:
        return relative_luminance(value) < 0.5
    except ThemeError:
        return True


def with_accent(palette_map: Mapping[str, str], accent: str) -> dict[str, str]:
    """A copy of ``palette_map`` whose accent family is retinted to ``accent``.

    The fill shade is darkened until white labels clear AA on it, and the text
    shade is pushed away from the page background the same way, so a light
    accent (sunflower) cannot produce unreadable buttons or links.  The rest of
    the palette is returned untouched; an unusable ``accent`` is ignored.
    """
    colors = dict(palette_map)
    value = str(accent or "").strip().lower()
    if not value.startswith("#"):
        value = "#" + value
    if not _HEX.fullmatch(value):
        return colors
    background = colors.get("bg", "#101216")
    surface = colors.get("surface", background)
    dark = _is_dark(background)

    fill = value
    for _ in range(12):
        if _reads_on("#ffffff", fill):
            break
        fill = _mix(fill, "#000000", 0.08)

    text = value
    for _ in range(12):
        if _reads_on(text, surface):
            break
        text = _mix(text, "#ffffff" if dark else "#000000", 0.10)

    colors["accent"] = value
    colors["accent_hover"] = _mix(value, "#ffffff" if dark else "#000000", 0.14)
    colors["accent_fill"] = fill
    colors["accent_fill_hover"] = _mix(fill, "#000000", 0.14)
    colors["on_accent"] = "#ffffff" if _reads_on("#ffffff", fill) else "#111111"
    colors["accent_text"] = text
    return colors


__all__ = [
    "ACCENT",
    "ACCENT_FILL",
    "ACCENT_FILL_HOVER",
    "CONTRAST_LARGE",
    "CONTRAST_TEXT",
    "CSS_NAMES",
    "DARK",
    "DEFAULT_MODE",
    "FAVORITE",
    "FILL_TEXT_TOKENS",
    "LIGHT",
    "MODES",
    "PALETTES",
    "RADIUS",
    "SPACE",
    "TEXT_TOKENS",
    "TYPE",
    "ThemeError",
    "contrast_ratio",
    "css_name",
    "css_variables",
    "is_dark_theme_name",
    "normalize_mode",
    "palette",
    "relative_luminance",
    "resolve",
    "with_accent",
]
