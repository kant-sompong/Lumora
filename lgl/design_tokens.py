"""The shared design tokens: one palette, one spacing scale, both apps.

Every colour, radius, spacing step and text size the two frontends draw with
comes from here, so the Linux GTK app and the Windows Qt app cannot drift into
two different designs.  Nothing in this module imports a GUI toolkit.

Two values are worth explaining:

* ``accent_soft`` is not a fourth surface; it is the accent at
  :data:`ACCENT_SOFT_ALPHA` opacity, composited by the caller, so a selected
  sidebar item or a season row inherits the theme's accent automatically.
* The dark ``muted`` value from the contract (#7c8090) reaches only 4.15:1 on
  the dark overlay colour, below the 4.5:1 floor.  It is lightened to
  ``#858998`` (4.68:1 on overlay, 5.10:1 on surface, 5.46:1 on bg), which is the
  nearest passing value in the same hue.
* No single purple can be both a fill that carries white text and accent-coloured
  text on a dark surface.  White on the fill shade (``#7c3aed``) is 5.70:1, but
  that same shade as ink on the dark overlay is only 2.86:1; the shade light
  enough to read as text (``#a78bfa``, 5.99:1 on the overlay) carries white at
  just 2.72:1.  The accent therefore has three roles, as the Linux palette
  already modelled: ``accent`` (the tint used for borders, washes and focus
  rings, 4.62:1 under white), ``accent_fill`` (the background under
  ``on_accent``) and ``accent_text`` (the same hue as readable text).
  ``danger``/``warning``/``success`` follow the same split: the bright value is
  the fill, and the ``*_text`` sibling is the ink that clears AA on the
  surfaces (dark danger 4.68:1 on overlay, light danger 6.47:1 on surface).
"""

from __future__ import annotations

#: Font sizes in pixels, shared by both frontends.
TEXT_SIZES: dict[str, int] = {
    "caption": 11,
    "body": 13,
    "label": 14,
    "title": 18,
    "header": 22,
}

#: The spacing scale, in pixels.  Anything else is a mistake, not a nuance.
SPACING: tuple[int, ...] = (4, 8, 12, 16, 24, 32)

#: The corner radii, in pixels.
RADIUS: tuple[int, ...] = (6, 10, 14)

#: ``accent_soft`` is the accent at this opacity, never an opaque colour.
ACCENT_SOFT_ALPHA = 0.18

#: At most three dark surface levels: bg, surface, overlay.
SURFACE_LEVELS: tuple[str, ...] = ("bg", "surface", "overlay")

TOKENS: dict[str, dict[str, object]] = {
    "dark": {
        "bg": "#0f1014",
        "surface": "#16181d",
        "overlay": "#1d2027",
        "border": "#2a2d36",
        "text": "#e8e9ee",
        "text2": "#a4a7b3",
        # Contract #7c8090 lightened to the nearest passing value (4.15 -> 4.68
        # on overlay); see the module docstring.
        "muted": "#858998",
        # The tint: borders, washes and focus rings.  Also fill-safe on its own
        # (white reaches 4.62:1), so a one-off accent fill never strands a label.
        "accent": "#8557ea",
        "accent_hover": "#9d74f8",
        "accent_fill": "#7c3aed",
        "accent_fill_hover": "#6d28d9",
        "accent_text": "#a78bfa",
        "accent_soft_alpha": ACCENT_SOFT_ALPHA,
        "success": "#22c55e",
        "success_text": "#22c55e",
        "warning": "#f59e0b",
        "warning_text": "#f59e0b",
        "danger": "#ef4444",
        "danger_text": "#f05252",
    },
    "light": {
        "bg": "#f4f5f8",
        "surface": "#ffffff",
        "overlay": "#ffffff",
        "border": "#dcdfe6",
        "text": "#14161c",
        "text2": "#4a4f5c",
        "muted": "#6b7080",
        "accent": "#7c3aed",
        "accent_hover": "#6d28d9",
        "accent_fill": "#7c3aed",
        "accent_fill_hover": "#6d28d9",
        "accent_text": "#6d28d9",
        "accent_soft_alpha": ACCENT_SOFT_ALPHA,
        "success": "#22c55e",
        "success_text": "#15803d",
        "warning": "#f59e0b",
        "warning_text": "#b45309",
        "danger": "#ef4444",
        "danger_text": "#b91c1c",
    },
}

#: Ratios are computed with the WCAG 2.1 relative-luminance definition.
_MIN_CONTRAST = 4.5


def _channels(colour: str) -> tuple[int, int, int]:
    text = str(colour or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6:
        raise ValueError(f"Not a hex colour: {colour!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"Not a hex colour: {colour!r}") from exc


def luminance(colour: str) -> float:
    """WCAG relative luminance of ``#rrggbb`` (0 = black, 1 = white)."""
    channels = []
    for value in _channels(colour):
        channel = value / 255
        channels.append(
            channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        )
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """The contrast ratio between two colours, between 1.0 and 21.0."""
    first, second = luminance(fg_hex), luminance(bg_hex)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def accent_soft(theme: str = "dark") -> str:
    """``rgba(...)`` for the accent at :data:`ACCENT_SOFT_ALPHA` opacity."""
    palette = TOKENS.get(theme) or TOKENS["dark"]
    red, green, blue = _channels(str(palette["accent"]))
    return f"rgba({red}, {green}, {blue}, {ACCENT_SOFT_ALPHA})"


__all__ = [
    "ACCENT_SOFT_ALPHA",
    "RADIUS",
    "SPACING",
    "SURFACE_LEVELS",
    "TEXT_SIZES",
    "TOKENS",
    "accent_soft",
    "contrast_ratio",
    "luminance",
]
