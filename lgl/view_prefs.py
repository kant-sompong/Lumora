"""Interface preferences that are not colours of their own: density, motion,
artwork backgrounds, spoiler hiding and the quick-preview popover.

Everything goes through :func:`set`, so a bad value from a settings file or a
hand-edited config cannot put the UI in a state it cannot draw.
"""

from __future__ import annotations

import re
from typing import Any

from . import theme

COMPACT = "compact_mode"
ACCENT = "accent_color"
TRANSITIONS = "page_transitions"
ARTWORK = "artwork_backgrounds"
SPOILERS = "spoiler_free"
PREVIEW = "quick_preview"

#: Transition amounts, coarsest first.
TRANSITION_CHOICES = ("off", "subtle", "full")
#: The accent the theme ships with, used when nothing valid is stored.
DEFAULT_ACCENT = theme.ACCENT

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

BOOLEANS = (COMPACT, ARTWORK, SPOILERS, PREVIEW)

#: Accents offered in the settings UI, all readable with white labels except
#: the light ones, which `readable_on` switches to dark text.
ACCENT_CHOICES: tuple[tuple[str, str], ...] = (
    ("#3b82f6", "Lumora blue"),
    ("#8b5cf6", "Violet"),
    ("#0f9d8f", "Teal"),
    ("#d97706", "Amber"),
    ("#e11d48", "Rose"),
    ("#facc15", "Sunflower"),
)


def _raw(settings: Any, key: str) -> Any:
    if settings is None:
        return None
    try:
        return settings.get(key)
    except Exception:  # noqa: BLE001 - a broken settings object is not worth a crash
        return None


def _stored_flag(settings: Any, key: str, default: bool) -> bool:
    value = _raw(settings, key)
    return default if value is None else bool(value)


def compact(settings: Any) -> bool:
    """Denser rows and smaller covers."""
    return _stored_flag(settings, COMPACT, False)


def set_compact(settings: Any, wanted: Any) -> bool:
    return _store(settings, COMPACT, bool(wanted))


def artwork_backgrounds(settings: Any) -> bool:
    """Blurred poster artwork behind a title page."""
    return _stored_flag(settings, ARTWORK, True)


def spoiler_free(settings: Any) -> bool:
    """Hide episode descriptions and revealing artwork."""
    return _stored_flag(settings, SPOILERS, False)


def quick_preview(settings: Any) -> bool:
    """Show the preview popover on selection."""
    return _stored_flag(settings, PREVIEW, True)


def transitions(settings: Any) -> str:
    value = str(_raw(settings, TRANSITIONS) or "subtle").strip().lower()
    return value if value in TRANSITION_CHOICES else "subtle"


def set_transitions(settings: Any, value: Any) -> str:
    chosen = str(value or "").strip().lower()
    if chosen not in TRANSITION_CHOICES:
        chosen = "subtle"
    _store(settings, TRANSITIONS, chosen)
    return chosen


def normalize_accent(value: Any) -> str:
    """A ``#rrggbb`` accent, or the theme's own when the value is not one."""
    match = _HEX.match(str(value or "").strip())
    return f"#{match.group(1).lower()}" if match else DEFAULT_ACCENT


def accent(settings: Any) -> str:
    return normalize_accent(_raw(settings, ACCENT) or DEFAULT_ACCENT)


def set_accent(settings: Any, value: Any) -> str:
    chosen = normalize_accent(value)
    _store(settings, ACCENT, chosen)
    return chosen


def readable_on(background: str) -> str:
    """White or near-black, whichever contrasts more with `background`."""
    white = theme.contrast_ratio("#ffffff", background)
    ink = theme.contrast_ratio("#111111", background)
    return "#ffffff" if white >= ink else "#111111"


def accent_text(settings: Any) -> str:
    """A readable label colour on top of the accent."""
    return readable_on(accent(settings))


def accent_is_readable(settings: Any) -> bool:
    """True when white text on the accent still passes AA for body text."""
    return theme.contrast_ratio("#ffffff", accent(settings)) >= 4.5


def effective(settings: Any) -> dict[str, Any]:
    """Every preference resolved, for a frontend to apply in one go."""
    return {
        COMPACT: compact(settings),
        ACCENT: accent(settings),
        "accent_text": accent_text(settings),
        TRANSITIONS: transitions(settings),
        ARTWORK: artwork_backgrounds(settings),
        SPOILERS: spoiler_free(settings),
        PREVIEW: quick_preview(settings),
    }


def _store(settings: Any, key: str, value: Any) -> bool:
    if settings is None:
        return False
    setter = getattr(settings, "set", None)
    if not callable(setter):
        return False
    setter(key, value)
    saver = getattr(settings, "save", None)
    if callable(saver):
        try:
            saver()
        except Exception:  # noqa: BLE001 - an unwritable config must not break the toggle
            return False
    return True


def set_spoilers(settings: Any, wanted: Any) -> bool:
    return _store(settings, SPOILERS, bool(wanted))


def set_artwork(settings: Any, wanted: Any) -> bool:
    return _store(settings, ARTWORK, bool(wanted))


def set_preview(settings: Any, wanted: Any) -> bool:
    return _store(settings, PREVIEW, bool(wanted))


__all__ = [
    "ACCENT",
    "ACCENT_CHOICES",
    "ARTWORK",
    "COMPACT",
    "DEFAULT_ACCENT",
    "PREVIEW",
    "SPOILERS",
    "TRANSITIONS",
    "TRANSITION_CHOICES",
    "accent",
    "accent_is_readable",
    "accent_text",
    "artwork_backgrounds",
    "compact",
    "effective",
    "normalize_accent",
    "quick_preview",
    "readable_on",
    "set_accent",
    "set_artwork",
    "set_compact",
    "set_preview",
    "set_spoilers",
    "set_transitions",
    "spoiler_free",
    "transitions",
]
