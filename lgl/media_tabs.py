"""Persistent, browser-style media tabs, shared by the Qt and GTK frontends.

The two frontends draw their own tab strip, but the *rules* of a tab — is this
title already open, which tab becomes active when one closes, what the small
grey line under the title says — live here, so Windows and Linux cannot drift
apart.

A tab is a plain :class:`TabState`.  The UI keeps the widgets (a Qt page, a GTK
detail page and player) next to it; this module never imports either toolkit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator


def format_position(seconds: object) -> str:
    """``754`` -> ``12:34``; zero or nonsense -> an empty string."""
    try:
        total = int(max(0.0, float(seconds or 0.0)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def with_start(url: str, position: object) -> str:
    """Add a media-fragment start hint so a resumed stream opens where it was.

    Only a real position is added, and only as the ``#t=`` fragment every
    media element understands; an embed that ignores it still loads normally.
    """
    text = str(url or "")
    try:
        seconds = int(float(position or 0.0))
    except (TypeError, ValueError):
        return text
    if seconds < 1 or not text:
        return text
    return f"{text.split('#', 1)[0]}#t={seconds}"


def subtitle_for(kind: str, season: object = None, episode: object = None) -> str:
    """The small grey line: ``S2 · EP5``, ``Movie``, ``S1 · EP12``.

    A season number alone still describes an episode selection, so it is shown
    the same way a full selection is; anything else falls back to the kind.
    """
    try:
        season_number = int(season) if season is not None else 0
    except (TypeError, ValueError):
        season_number = 0
    try:
        episode_number = int(episode) if episode is not None else 0
    except (TypeError, ValueError):
        episode_number = 0
    if season_number > 0 and episode_number > 0:
        return f"S{season_number} · EP{episode_number}"
    if season_number > 0:
        return f"S{season_number}"
    clean = str(kind or "").strip().lower()
    if clean in ("movie", "film"):
        return "Movie"
    if clean in ("tv", "show", "series"):
        return "TV Show"
    if clean == "anime":
        return "Anime"
    return clean.capitalize() or "Media"


@dataclass
class TabState:
    """One open media tab: identity, selection, and what to restore.

    ``payload`` carries whatever a frontend needs to rebuild the page without
    asking the network again (a Qt ``Card``, a GTK ``MediaItem`` plus its
    already-fetched details).
    """

    tab_id: str
    key: str
    title: str
    kind: str = ""
    season: int | None = None
    episode: int | None = None
    scroll: int = 0
    playing: bool = False
    position: float = 0.0
    url: str = ""
    provider: str = ""
    section: str = ""
    payload: dict = field(default_factory=dict)

    @property
    def subtitle(self) -> str:
        return subtitle_for(self.kind, self.season, self.episode)

    def copy(self) -> "TabState":
        return replace(self, payload=dict(self.payload))


def _new_id() -> str:
    return uuid.uuid4().hex


class TabStore:
    """The ordered list of open tabs and which one is in front.

    Opening a title that is already open activates its tab instead of adding a
    duplicate; closing the active tab picks its right-hand neighbour, then its
    left-hand one, then nothing.
    """

    def __init__(self, tabs: Iterable[TabState] | None = None,
                 active_id: str | None = None) -> None:
        self._tabs: list[TabState] = [tab.copy() for tab in (tabs or ())]
        self._active = active_id if self.find_id(active_id) else (
            self._tabs[0].tab_id if self._tabs else None
        )

    # reading ---------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._tabs)

    def __iter__(self) -> Iterator[TabState]:
        return iter(self.tabs)

    @property
    def tabs(self) -> list[TabState]:
        """Copies, so a caller cannot edit stored state by accident."""
        return [tab.copy() for tab in self._tabs]

    @property
    def empty(self) -> bool:
        return not self._tabs

    def get(self, tab_id: str | None) -> TabState | None:
        return self.find_id(tab_id)

    def find_id(self, tab_id: str | None) -> TabState | None:
        if not tab_id:
            return None
        for tab in self._tabs:
            if tab.tab_id == tab_id:
                return tab
        return None

    def find(self, key: str) -> TabState | None:
        for tab in self._tabs:
            if tab.key == key:
                return tab
        return None

    @property
    def active(self) -> TabState | None:
        return self.find_id(self._active)

    @property
    def active_id(self) -> str | None:
        return self._active

    def index_of(self, tab_id: str | None) -> int:
        for index, tab in enumerate(self._tabs):
            if tab.tab_id == tab_id:
                return index
        return -1

    # writing ---------------------------------------------------------------
    def open(self, key: str, title: str, kind: str = "",
             *, tab_id: str | None = None) -> tuple[TabState, bool]:
        """Open (or select) the tab for ``key``; ``created`` says which."""
        existing = self.find(key)
        if existing is not None:
            self._active = existing.tab_id
            return existing, False
        tab = TabState(
            tab_id=tab_id or _new_id(), key=str(key), title=str(title or ""),
            kind=str(kind or ""),
        )
        self._tabs.append(tab)
        self._active = tab.tab_id
        return tab, True

    def activate(self, tab_id: str | None) -> TabState | None:
        tab = self.find_id(tab_id)
        if tab is not None:
            self._active = tab.tab_id
        return tab

    def close(self, tab_id: str) -> TabState | None:
        """Close ``tab_id`` and return the tab that should be shown now.

        Closing an inactive tab leaves the active one alone.  Closing the
        active tab moves to the tab that slid into its place, or the one before
        it, and closing the last tab leaves nothing active.
        """
        index = self.index_of(tab_id)
        if index < 0:
            return self.active
        was_active = self._tabs[index].tab_id == self._active
        del self._tabs[index]
        if not was_active:
            return self.active
        if not self._tabs:
            self._active = None
            return None
        successor = self._tabs[min(index, len(self._tabs) - 1)]
        self._active = successor.tab_id
        return successor

    def update(self, tab_id: str, **fields: object) -> TabState | None:
        """Store part of a tab's restorable state (season, scroll, position…)."""
        tab = self.find_id(tab_id)
        if tab is None:
            return None
        for name, value in fields.items():
            if name in ("tab_id", "key"):
                continue
            if hasattr(tab, name):
                setattr(tab, name, value)
        return tab

    def clear(self) -> None:
        self._tabs.clear()
        self._active = None


__all__ = [
    "TabState", "TabStore", "format_position", "subtitle_for", "with_start",
]
