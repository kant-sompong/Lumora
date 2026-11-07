"""Which shelves the home page shows, in what order, and how big.

Roadmap section 19: reorder or hide shelves, offer compact and poster layouts,
persist the choice, and provide a reset.
"""

from __future__ import annotations

from typing import Any

SETTINGS_KEY = "home_layout"
POSTER = "poster"
LARGE = "large"
COMPACT = "compact"
LAYOUTS = (POSTER, LARGE, COMPACT)
CHOICES = ((POSTER, "Poster"), (LARGE, "Large"), (COMPACT, "Compact"))
#: Card width multiplier per layout; the grid multiplies its base width by this.
SCALES = {POSTER: 1.0, LARGE: 1.3, COMPACT: 0.78}
MAX_SHELVES = 12

#: Per-shelf size, set on the Home layout panel.  Separate from the global
#: card layout above: every section can be small, normal or large on its own.
SIZE_COMPACT = COMPACT
SIZE_NORMAL = "normal"
SIZE_LARGE = LARGE
SIZES = (SIZE_COMPACT, SIZE_NORMAL, SIZE_LARGE)
SIZE_DEFAULT = SIZE_NORMAL
SIZE_CHOICES = ((SIZE_COMPACT, "Compact"), (SIZE_NORMAL, "Normal"), (SIZE_LARGE, "Large"))
SIZE_SCALES = {SIZE_COMPACT: 0.78, SIZE_NORMAL: 1.0, SIZE_LARGE: 1.3}
#: Shelf chip height and how many titles fit in one section, per size.
SIZE_HEIGHTS = {SIZE_COMPACT: 26, SIZE_NORMAL: 32, SIZE_LARGE: 40}
SIZE_TITLES = {SIZE_COMPACT: 4, SIZE_NORMAL: 6, SIZE_LARGE: 8}

#: Shelf id -> label shown in the customiser.
SHELVES: tuple[tuple[str, str], ...] = (
    ("continue", "Continue watching"),
    ("favorites", "Favourites"),
    ("recent", "Recently watched"),
    ("collections", "Collections"),
    ("downloads", "Recent downloads"),
    ("catalogue", "New in the catalogue"),
)
_LABELS = dict(SHELVES)
_IDS = [shelf for shelf, _ in SHELVES]
DEFAULT_ORDER = list(_IDS)


def label(shelf: str) -> str:
    return _LABELS.get(shelf, shelf)


def scale(value: Any) -> float:
    """Card width multiplier for a layout name; unknown values use the poster size."""
    return SCALES.get(layout_of(value), SCALES[POSTER])


def layout_of(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in LAYOUTS else POSTER


def size_of(value: Any) -> str:
    """A stored per-shelf size; anything unknown means Normal."""
    text = str(value or "").strip().lower()
    return text if text in SIZES else SIZE_DEFAULT


def size_scale(value: Any) -> float:
    return SIZE_SCALES[size_of(value)]


def size_height(value: Any) -> int:
    return SIZE_HEIGHTS[size_of(value)]


def shelf_titles(value: Any) -> int:
    """How many titles a section shows at this size."""
    return SIZE_TITLES[size_of(value)]


def shelf_for_label(name: Any) -> str:
    """The shelf id behind a heading, or "" for a user-made shelf."""
    text = str(name or "").strip()
    for shelf, label_text in SHELVES:
        if label_text == text:
            return shelf
    return ""


class HomeLayout:
    """Persisted shelf arrangement, backed by the settings store."""

    def __init__(self, settings: Any):
        self.settings = settings

    def _state(self) -> dict[str, Any]:
        raw = self.settings.get(SETTINGS_KEY) if self.settings is not None else None
        return dict(raw) if isinstance(raw, dict) else {}

    def _write(self, state: dict[str, Any]) -> None:
        self.settings.set(SETTINGS_KEY, state)

    def order(self) -> list[str]:
        """Visible shelves in display order, always including every known shelf."""
        state = self._state()
        stored = state.get("order")
        hidden = state.get("hidden")
        hidden_set = {str(item) for item in hidden} if isinstance(hidden, list) else set()
        ordered: list[str] = []
        if isinstance(stored, list):
            for item in stored:
                text = str(item)
                if text in _LABELS and text not in ordered:
                    ordered.append(text)
        for shelf in _IDS:
            if shelf not in ordered:
                ordered.append(shelf)
        return [shelf for shelf in ordered if shelf not in hidden_set]

    def hidden(self) -> list[str]:
        state = self._state()
        raw = state.get("hidden")
        return [str(item) for item in raw] if isinstance(raw, list) else []

    def layout(self) -> str:
        return layout_of(self._state().get("layout"))

    def sizes(self) -> dict[str, str]:
        """Every shelf's size; an older layout without sizes reads as Normal."""
        raw = self._state().get("sizes")
        stored = raw if isinstance(raw, dict) else {}
        return {shelf: size_of(stored.get(shelf)) for shelf in _IDS}

    def size(self, shelf: str) -> str:
        return self.sizes().get(shelf, SIZE_DEFAULT)

    def set_size(self, shelf: str, value: Any) -> str:
        """Store one shelf's size; returns the stored name, or "" if unknown."""
        if shelf not in _LABELS:
            return ""
        chosen = size_of(value)
        state = self._state()
        stored = state.get("sizes")
        sizes = dict(stored) if isinstance(stored, dict) else {}
        sizes[shelf] = chosen
        state["sizes"] = sizes
        self._write(state)
        return chosen

    def set_layout(self, value: str) -> str:
        state = self._state()
        state["layout"] = layout_of(value)
        self._write(state)
        return state["layout"]

    def move(self, shelf: str, index: int) -> bool:
        if shelf not in _LABELS:
            return False
        current = self.order()
        try:
            position = max(0, min(len(current) - 1, int(index)))
        except (TypeError, ValueError):
            return False
        if shelf not in current:
            return False
        current.remove(shelf)
        current.insert(position, shelf)
        state = self._state()
        state["order"] = current
        self._write(state)
        return True

    def hide(self, shelf: str) -> bool:
        if shelf not in _LABELS:
            return False
        hidden = self.hidden()
        if shelf in hidden:
            return True
        if len(self.order()) <= 1:
            return False
        hidden.append(shelf)
        state = self._state()
        state["hidden"] = hidden
        self._write(state)
        return True

    def show(self, shelf: str) -> bool:
        if shelf not in _LABELS:
            return False
        hidden = self.hidden()
        if shelf not in hidden:
            return True
        state = self._state()
        state["hidden"] = [item for item in hidden if item != shelf]
        self._write(state)
        return True

    def toggle(self, shelf: str, visible: bool) -> bool:
        return self.show(shelf) if visible else self.hide(shelf)

    def reset(self) -> None:
        self.settings.set(SETTINGS_KEY, {})

    def as_dict(self) -> dict[str, Any]:
        return {"order": self.order(), "hidden": self.hidden(), "layout": self.layout()}


__all__ = [
    "CHOICES",
    "COMPACT",
    "DEFAULT_ORDER",
    "LARGE",
    "LAYOUTS",
    "POSTER",
    "SETTINGS_KEY",
    "SHELVES",
    "SIZES",
    "SIZE_CHOICES",
    "SIZE_COMPACT",
    "SIZE_DEFAULT",
    "SIZE_HEIGHTS",
    "SIZE_LARGE",
    "SIZE_NORMAL",
    "SIZE_SCALES",
    "SIZE_TITLES",
    "HomeLayout",
    "label",
    "layout_of",
    "scale",
    "shelf_for_label",
    "shelf_titles",
    "size_height",
    "size_of",
    "size_scale",
]
