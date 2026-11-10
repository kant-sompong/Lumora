"""Where every screen lives in the window: the rail, the page tabs and the menu.

Pure data, with no Qt and no GTK import, so the Windows frontend builds its
navigation from this map and the Linux frontend can reuse it later.  ``PLACES``
records where each entry of the old 22-item header menu went; the tests use it
to prove that nothing was lost in the move.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Left rail, top to bottom.
RAIL: tuple[tuple[str, str], ...] = (
    ("browse", "Browse"),
    ("library", "Library"),
    ("media", "Media"),
    ("downloads", "Downloads"),
)

#: Pinned to the bottom of the rail.
RAIL_BOTTOM: tuple[tuple[str, str], ...] = (
    ("settings", "Settings"),
    ("about", "About"),
)

#: Tabs across the top of each page, in order.
TABS: dict[str, tuple[tuple[str, str], ...]] = {
    "library": (
        ("games", "Games"),
        ("favourites", "Favourites"),
        ("collections", "Collections"),
        ("activity", "Activity"),
        ("statistics", "Statistics"),
        ("recent", "Recently viewed"),
        ("complete-the-set", "Complete the set"),
    ),
    "media": (
        ("home", "Home"),
        ("movies", "Movies"),
        ("tv", "TV Shows"),
        ("anime", "Anime"),
        ("list", "My List"),
        ("history", "History"),
        ("calendar", "Calendar"),
        ("compare", "Compare seasons"),
    ),
}

#: What the header menu keeps, and nothing else.
MENU: tuple[tuple[str, str], ...] = (
    ("palette", "Command palette…"),
    ("profiles", "Profiles…"),
    ("shortcuts", "Keyboard shortcuts…"),
    ("tools", "Tools…"),
    ("sources", "Website Sources…"),
    ("kryoto", "Kryoto Catalog…"),
    ("queue", "Show Download Queue"),
    ("maintenance", "Maintenance"),
    ("settings", "Settings…"),
    ("about", "About Lumora"),
)

#: Where each entry the menu used to hold now lives.
#:
#: ``kind`` is ``menu`` (still in the header menu), ``page`` (a tab inside a
#: page), ``dialog`` (still a separate window) or ``action`` (a button).
PLACES: dict[str, tuple[str, str | None, str | None]] = {
    "palette": ("menu", None, None),
    "profiles": ("dialog", "profiles", None),
    "shortcuts": ("dialog", "shortcuts", None),
    "tools": ("dialog", "tools", None),
    "sources": ("menu", None, None),
    "kryoto": ("menu", None, None),
    "queue": ("page", "downloads", None),
    "maintenance": ("menu", None, None),
    "folder": ("menu", None, None),
    "updates": ("dialog", "tools", None),
    "calendar": ("page", "media", "calendar"),
    "seasons": ("page", "media", "compare"),
    "viewed": ("page", "library", "recent"),
    "surprise": ("action", "surprise", None),
    "statistics": ("page", "library", "statistics"),
    # Tagging is a right-click action now, so the old menu entry opens a dialog.
    "tags": ("dialog", "tags", None),
    "details": ("dialog", "metadata", None),
    "bulk": ("page", "library", "games"),
    "duplicates": ("dialog", "duplicates", None),
    # Discover is not a page any more: its two ranked rows are on Browse and
    # the collection gaps are a Library tab.
    "discover": ("page", "browse", None),
    "favourites": ("page", "library", "favourites"),
    "activity": ("page", "library", "activity"),
    "collections": ("page", "library", "collections"),
    "settings": ("menu", None, None),
    "about": ("menu", None, None),
}


def rail_keys() -> tuple[str, ...]:
    return tuple(key for key, _label in RAIL)


def page_keys() -> tuple[str, ...]:
    return tuple(key for key, _label in RAIL)


def tabs_for(page: str) -> tuple[tuple[str, str], ...]:
    return TABS.get(str(page), ())


def tab_keys(page: str) -> tuple[str, ...]:
    return tuple(key for key, _label in tabs_for(page))


def tab_label(page: str, tab: str) -> str:
    for key, label in tabs_for(page):
        if key == tab:
            return label
    return ""


def has_tab(page: str, tab: str) -> bool:
    return str(tab) in tab_keys(page)


def menu_keys() -> tuple[str, ...]:
    return tuple(key for key, _label in MENU)


def menu_labels() -> tuple[str, ...]:
    return tuple(label for _key, label in MENU)


def place(entry: str) -> tuple[str, str | None, str | None]:
    """Where ``entry`` lives now; a missing entry is a ``KeyError``."""
    return PLACES[str(entry)]


def page_place(entry: str) -> tuple[str, str]:
    """The ``(page, tab)`` an entry opens, or its page with an empty tab."""
    kind, page, tab = place(entry)
    if kind not in ("page", "action"):
        raise ValueError(f"{entry!r} does not open a page")
    return (page or "", tab or "")


def entries_on(page: str, tab: str | None = None) -> list[str]:
    """The old menu entries that now live on ``page`` (and ``tab``)."""
    found = []
    for entry, (kind, where, which) in PLACES.items():
        if kind not in ("page", "action") or where != page:
            continue
        if tab is not None and which != tab:
            continue
        found.append(entry)
    return sorted(found)


def menu_for(entries: Iterable[str]) -> list[str]:
    """The labels the header menu should show, given the entries it has."""
    wanted = set(entries)
    return [label for key, label in MENU if key in wanted]


__all__ = [
    "MENU",
    "PLACES",
    "RAIL",
    "RAIL_BOTTOM",
    "TABS",
    "entries_on",
    "has_tab",
    "menu_for",
    "menu_keys",
    "menu_labels",
    "page_keys",
    "page_place",
    "place",
    "rail_keys",
    "tab_keys",
    "tab_label",
    "tabs_for",
]
