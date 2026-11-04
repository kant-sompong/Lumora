"""Keyboard shortcuts, accessible labels and contrast checks.

Roadmap section 11 asks for shortcuts to search and navigation, keyboard
operation of menus/dialogs/cards, visible focus, accessible labels on icon-only
controls, respect for system scaling, and contrast that holds up in every theme.

The two frontends spell accelerators differently (``<Control>f`` on GTK,
``Ctrl+F`` on Qt), so each shortcut carries both spellings and the UI only picks
one.  The contrast helpers work on the palette values both themes already use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

GTK = "gtk"
QT = "qt"
TOOLKITS = (GTK, QT)

# Shortcut identifiers.
SEARCH = "search"
CLOSE = "close"
REFRESH = "refresh"
BACK = "back"
DOWNLOADS = "downloads"
LIBRARY = "library"
MEDIA = "media"
SETTINGS = "settings"
SHORTCUT_HELP = "shortcut-help"
PLAY_PAUSE = "play-pause"
NEXT_PAGE = "next-page"
PREVIOUS_PAGE = "previous-page"
DELETE = "delete"


@dataclass(frozen=True)
class Shortcut:
    id: str
    label: str
    keys_gtk: str
    keys_qt: str
    group: str = "General"
    description: str = ""

    def keys(self, toolkit: str = GTK) -> str:
        return self.keys_qt if str(toolkit).lower() == QT else self.keys_gtk


SHORTCUTS: tuple[Shortcut, ...] = (
    Shortcut(SEARCH, "Focus search", "<Control>f", "Ctrl+F", "Search",
             "Puts the cursor in the search box from anywhere in the window."),
    Shortcut(SHORTCUT_HELP, "Keyboard shortcuts", "<Control>question", "Ctrl+?", "Help",
             "Shows this list."),
    Shortcut(CLOSE, "Close window or dialog", "Escape", "Esc", "General",
             "Closes the open dialog, or the window when nothing is open."),
    Shortcut(REFRESH, "Refresh", "<Control>r", "Ctrl+R", "General",
             "Reloads the current page."),
    Shortcut(BACK, "Back", "<Alt>Left", "Alt+Left", "General",
             "Returns to the previous page."),
    Shortcut(LIBRARY, "Go to library", "<Control>1", "Ctrl+1", "Navigation",
             "Opens the game library."),
    Shortcut(MEDIA, "Go to movies and TV", "<Control>2", "Ctrl+2", "Navigation",
             "Opens the movies and TV page."),
    Shortcut(DOWNLOADS, "Go to downloads", "<Control>3", "Ctrl+3", "Navigation",
             "Opens the download dashboard."),
    Shortcut(SETTINGS, "Open settings", "<Control>comma", "Ctrl+,", "Navigation",
             "Opens settings."),
    Shortcut(PLAY_PAUSE, "Play or pause", "space", "Space", "Playback",
             "Toggles playback in the player."),
    Shortcut(PREVIOUS_PAGE, "Previous page", "<Control>Page_Up", "Ctrl+PageUp", "Lists",
             "Moves to the previous page of results."),
    Shortcut(NEXT_PAGE, "Next page", "<Control>Page_Down", "Ctrl+PageDown", "Lists",
             "Moves to the next page of results."),
    Shortcut(DELETE, "Remove selected item", "Delete", "Del", "Lists",
             "Removes the selected item, asking first."),
)

_BY_ID = {item.id: item for item in SHORTCUTS}


def all_shortcuts() -> tuple[Shortcut, ...]:
    return SHORTCUTS


def lookup(shortcut_id: str) -> Shortcut | None:
    return _BY_ID.get(str(shortcut_id or "").strip().lower())


def accelerator(shortcut_id: str, toolkit: str = GTK) -> str:
    item = lookup(shortcut_id)
    return item.keys(toolkit) if item else ""


def groups() -> list[tuple[str, list[Shortcut]]]:
    out: dict[str, list[Shortcut]] = {}
    for item in SHORTCUTS:
        out.setdefault(item.group, []).append(item)
    return list(out.items())


def help_text(toolkit: str = GTK) -> str:
    lines = []
    for group, items in groups():
        lines.append(group)
        for item in items:
            lines.append(f"  {item.keys(toolkit):<14} {item.label}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def accessible(text: str, description: str = "") -> dict[str, str]:
    """Label/description pair for an icon-only control.

    The label is mandatory: a screen reader needs *something* to announce, so a
    blank one falls back to a visible description or a generic noun.
    """
    label = str(text or "").strip() or str(description or "").strip() or "Button"
    return {"label": label[:120], "description": str(description or "").strip()[:300]}


def focus_order(widgets: Sequence[object]) -> list[object]:
    """Widgets in tab order, skipping anything explicitly unfocusable."""
    return [widget for widget in widgets if getattr(widget, "can_focus", True) is not False]


def _channel(value: int) -> float:
    return value / 255.0


def _luminance(colour: str) -> float:
    text = str(colour or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return 0.0
    try:
        red, green, blue = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return 0.0

    def linear(channel: int) -> float:
        value = _channel(channel)
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` colours."""
    first = _luminance(foreground)
    second = _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def readable_text_on(background: str) -> str:
    """Black or white, whichever reads better on this background."""
    return "#111111" if contrast_ratio("#111111", background) >= contrast_ratio("#ffffff", background) else "#ffffff"


def check_theme(pairs: Iterable[tuple[str, str]], *, minimum: float = 4.5) -> list[str]:
    """Report palette pairs that fail the WCAG AA text threshold."""
    problems = []
    for foreground, background in pairs:
        ratio = contrast_ratio(foreground, background)
        if ratio < minimum:
            problems.append(f"{foreground} on {background} is {ratio}:1, below {minimum}:1")
    return problems


__all__ = [
    "BACK",
    "CLOSE",
    "DELETE",
    "DOWNLOADS",
    "GTK",
    "LIBRARY",
    "MEDIA",
    "NEXT_PAGE",
    "PLAY_PAUSE",
    "PREVIOUS_PAGE",
    "QT",
    "REFRESH",
    "SEARCH",
    "SETTINGS",
    "SHORTCUTS",
    "SHORTCUT_HELP",
    "Shortcut",
    "TOOLKITS",
    "accessible",
    "accelerator",
    "all_shortcuts",
    "check_theme",
    "contrast_ratio",
    "focus_order",
    "groups",
    "help_text",
    "lookup",
    "readable_text_on",
]
