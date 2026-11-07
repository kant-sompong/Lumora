"""The GTK 3 Favourites and Activity windows.

The rules live in :mod:`lgl.stats` (``split`` and ``timeline``) so both
frontends agree; these windows only gather rows and draw them.  GTK 3 only:
``pack_start``, ``.add()`` and ``get_style_context().add_class()``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import recently_viewed, stats

#: ``(field, verb)`` pairs turned into timeline events for a library entry.
#: ``last_seen`` is deliberately absent: every library scan moves it to now, so
#: it would bury real activity under a wall of identical "Updated" rows.
ENTRY_EVENTS = (
    ("added_at", "Added"),
    ("last_played", "Launched"),
)

#: Stored kinds that belong in the media column, whatever the history says.
MEDIA_KINDS = ("movie", "show", "anime", "episode", "tv")

#: How many timeline rows a window shows.
TIMELINE_LIMIT = 60


def as_row(entry: Any) -> dict[str, Any]:
    """A mapping view of a ``LibraryEntry`` or a plain dict."""
    if isinstance(entry, Mapping):
        row = dict(entry)
    else:
        row = {
            "key": getattr(entry, "identifier", ""),
            "title": getattr(entry, "title", ""),
            "kind": "game",
            "favorite": bool(getattr(entry, "favorite", False)),
            "added_at": getattr(entry, "added_at", 0.0),
            "last_seen": getattr(entry, "last_seen", 0.0),
            "last_played": getattr(entry, "last_played", 0.0),
        }
    row.setdefault("key", row.get("identifier", ""))
    return row


def library_entries(parent: Any) -> list[Any]:
    """Everything the library index holds, favourite flag included."""
    store = getattr(parent, "library_store", None)
    entries = getattr(store, "entries", None)
    if callable(entries):
        try:
            return list(entries())
        except Exception:  # noqa: BLE001 - a broken index shows an empty window
            return []
    view = getattr(parent, "library_view", None)
    return list(getattr(view, "_entries", None) or [])


def favourite_rows(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """The favourite games, tagged so ``stats.split`` can bucket them."""
    rows = []
    for entry in entries:
        row = as_row(entry)
        if not row.get("favorite"):
            continue
        row["kind"] = "game"
        rows.append(row)
    return rows


def build_events(
    entries: Iterable[Any], viewed: Iterable[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Added and launched games plus the viewing history.

    ``stats.timeline`` drops the events whose timestamp is junk and sorts what
    is left, so nothing here needs to validate or order anything.
    """
    events: list[dict[str, Any]] = []
    for entry in entries:
        row = as_row(entry)
        title = str(row.get("title") or row.get("key") or "").strip()
        for field, verb in ENTRY_EVENTS:
            stamp = row.get(field)
            if stamp:
                events.append(
                    {
                        "ts": stamp,
                        "kind": "game",
                        "label": f"{verb} · {title}" if title else verb,
                    }
                )
    for item in viewed:
        stamp = item.get("viewed_at")
        if not stamp:
            continue
        title = str(item.get("title") or item.get("key") or "").strip()
        events.append(
            {
                "ts": stamp,
                "kind": str(item.get("kind") or "media"),
                "label": f"Watched · {title}" if title else "Watched",
            }
        )
    return events


def _note(text: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_max_width_chars(64)
    label.get_style_context().add_class("dim-label")
    return label


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.get_style_context().add_class("panel-title")
    return label


def _frame(window: Gtk.Window, width: int, height: int) -> Gtk.Box:
    window.set_default_size(width, height)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for side in ("top", "bottom", "start", "end"):
        getattr(outer, f"set_margin_{side}")(12)
    window.add(outer)
    return outer


def _scroller(widget: Gtk.Widget) -> Gtk.ScrolledWindow:
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.add(widget)
    return scroller


def _close_button(window: Gtk.Window) -> Gtk.Button:
    close = Gtk.Button(label="Close")
    close.connect("clicked", lambda *_: window.close())
    return close


class FavouritesWindow(Gtk.Window):
    """Favourite games beside favourite media, split by ``stats.split``."""

    def __init__(self, parent: Any, rows: Iterable[Mapping[str, Any]]) -> None:
        super().__init__(title="Favourites")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        buckets = stats.split([as_row(row) for row in rows])
        self.games = list(buckets.get("game", []))
        self.media = [
            row
            for kind, items in buckets.items()
            if kind != "game"
            for row in items
        ]
        outer = _frame(self, 620, 460)
        outer.pack_start(_heading("FAVOURITES"), False, False, 0)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        columns.set_homogeneous(True)
        games_column, self.games_list, self.games_note = self._column(
            "Games", self.games
        )
        media_column, self.media_list, self.media_note = self._column(
            "Movies, series and anime", self.media
        )
        columns.pack_start(games_column, True, True, 0)
        columns.pack_start(media_column, True, True, 0)
        outer.pack_start(columns, True, True, 0)
        self.columns = columns
        self.games_column = games_column
        self.media_column = media_column

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.pack_end(_close_button(self), False, False, 0)
        outer.pack_start(buttons, False, False, 0)

        if not self.games:
            self.games_note.set_text(
                "No favourites yet. Heart a game in the Library view and it "
                "shows up here."
            )
        if not self.media:
            self.media_note.set_text(
                "Media favourites are not stored anywhere yet, so this column "
                "stays empty."
            )

    def _column(
        self, name: str, rows: list[Mapping[str, Any]]
    ) -> tuple[Gtk.Box, Gtk.ListBox, Gtk.Label]:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.pack_start(_heading(f"{name.upper()} ({len(rows)})"), False, False, 0)
        listing = Gtk.ListBox()
        listing.set_selection_mode(Gtk.SelectionMode.NONE)
        for row in rows:
            listing.add(self._row(row))
        column.pack_start(_scroller(listing), True, True, 0)
        note = _note()
        column.pack_start(note, False, False, 0)
        return column, listing, note

    def _row(self, row: Mapping[str, Any]) -> Gtk.ListBoxRow:
        item = Gtk.ListBoxRow()
        label = Gtk.Label(
            label=str(row.get("title") or row.get("key") or "Untitled"),
            xalign=0,
        )
        label.set_line_wrap(True)
        label.set_margin_start(4)
        label.set_margin_end(4)
        label.set_margin_top(2)
        label.set_margin_bottom(2)
        item.add(label)
        item.row_data = dict(row)  # type: ignore[attr-defined]
        return item


class ActivityWindow(Gtk.Window):
    """What was added, updated, launched or watched, newest first."""

    def __init__(
        self, parent: Any, events: Iterable[Mapping[str, Any]]
    ) -> None:
        super().__init__(title="Activity")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self.rows = stats.timeline(list(events), limit=TIMELINE_LIMIT)
        outer = _frame(self, 540, 480)
        outer.pack_start(_heading("ACTIVITY"), False, False, 0)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        for day, label in self.rows:
            item = Gtk.ListBoxRow()
            text = Gtk.Label(label=f"{day}  ·  {label}", xalign=0)
            text.set_line_wrap(True)
            text.set_margin_start(4)
            text.set_margin_end(4)
            item.add(text)
            self.list.add(item)
        outer.pack_start(_scroller(self.list), True, True, 0)

        self.empty = _note(
            ""
            if self.rows
            else "Nothing recorded yet: no title has been added, updated, "
            "launched or watched."
        )
        outer.pack_start(self.empty, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.pack_end(_close_button(self), False, False, 0)
        outer.pack_start(buttons, False, False, 0)


def show_favourites(parent: Any) -> FavouritesWindow:
    """Open Favourites for the current library."""
    window = FavouritesWindow(parent, favourite_rows(library_entries(parent)))
    window.show_all()
    window.present()
    return window


def show_activity(parent: Any) -> ActivityWindow:
    """Open the Activity timeline for the library and viewing history."""
    entries = library_entries(parent)
    viewed = recently_viewed.recent(recently_viewed.default_path(), 200)
    window = ActivityWindow(parent, build_events(entries, viewed))
    window.show_all()
    window.present()
    return window


__all__ = [
    "ActivityWindow",
    "FavouritesWindow",
    "as_row",
    "build_events",
    "favourite_rows",
    "library_entries",
    "show_activity",
    "show_favourites",
]
