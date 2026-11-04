"""Which shelves the home page shows, in what order, and how big.

Roadmap section 19: reorder or hide shelves, offer compact and poster layouts,
persist the choice, and provide a reset.
"""

from __future__ import annotations

from typing import Any

SETTINGS_KEY = "home_layout"
POSTER = "poster"
COMPACT = "compact"
LAYOUTS = (POSTER, COMPACT)
MAX_SHELVES = 12

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


def layout_of(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in LAYOUTS else POSTER


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
    "COMPACT",
    "DEFAULT_ORDER",
    "LAYOUTS",
    "POSTER",
    "SETTINGS_KEY",
    "SHELVES",
    "HomeLayout",
    "label",
    "layout_of",
]
