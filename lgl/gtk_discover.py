"""The GTK 3 Discover window: recommendations, similar titles, hidden gems and
collection gaps, all ranked by :mod:`lgl.discovery`.

The ranking is shared with the Qt frontend; this file only turns the loaded
catalogue and the user's library into the mappings the ranker expects and draws
the lists.  GTK 3 only: ``pack_start``, ``.add()`` and
``get_style_context().add_class()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import discovery


def game_rows(games: Sequence[Any]) -> list[dict[str, Any]]:
    """The loaded catalogue as the mappings :mod:`lgl.discovery` ranks."""
    rows: list[dict[str, Any]] = []
    for game in games:
        key = str(getattr(game, "identifier", "") or "").strip()
        if not key:
            continue
        rows.append(
            {
                "key": key,
                "title": str(getattr(game, "title", "") or ""),
                "year": str(getattr(game, "year", "") or ""),
                "genres": list(getattr(game, "subjects", []) or []),
                "rating": getattr(game, "rating", None),
                "popularity": getattr(game, "popularity", None),
            }
        )
    return rows


def library_keys(parent: Any) -> list[str]:
    """Identifiers of every title the user keeps in their library."""
    view = getattr(parent, "library_view", None)
    entries = getattr(view, "_entries", None) or []
    keys: list[str] = []
    for entry in entries:
        key = str(getattr(entry, "identifier", "") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def row_text(item: Mapping[str, Any]) -> str:
    """One readable entry: the title, then year and genres when known."""
    title = str(item.get("title") or item.get("key") or "Untitled").strip()
    genres = item.get("genres") or ()
    if isinstance(genres, str):
        genres = [part for part in genres.replace(";", ",").split(",")]
    labels = " · ".join(str(part).strip() for part in genres if str(part).strip())
    year = str(item.get("year") or "").strip()
    details = " · ".join(part for part in (year, labels) if part)
    return f"{title}\n{details}" if details else title


class DiscoverWindow(Gtk.Window):
    """Ways to find the next title, from what is loaded and what is owned."""

    def __init__(
        self,
        parent: Any,
        settings: Any,
        items: Sequence[Mapping[str, Any]],
        *,
        liked: Sequence[str] = (),
        owned: Sequence[str] = (),
    ) -> None:
        super().__init__(title="Discover")
        self.set_default_size(620, 560)
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self._parent = parent
        self.settings = settings
        self.items = [dict(item) for item in items]
        self.liked = [str(key) for key in liked]
        self.owned = [str(key) for key in owned]
        self._games: dict[str, Any] = {}
        for game in getattr(parent, "_games", None) or []:
            key = str(getattr(game, "identifier", "") or "")
            if key:
                self._games[key] = game
        self._lists: dict[str, Gtk.ListBox] = {}
        self._notes: dict[str, Gtk.Label] = {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)

        heading = Gtk.Label(label="Discover", xalign=0)
        heading.get_style_context().add_class("panel-title")
        outer.pack_start(heading, False, False, 0)

        self.notebook = Gtk.Notebook()
        outer.pack_start(self.notebook, True, True, 0)

        self._simple_page(
            "For you",
            discovery.recommend(self.items, self.liked),
            "Like what you already have; nothing you own is repeated.",
            empty_note=(
                "No recommendations from the loaded titles yet."
                if self.liked
                else "Mark favourites to get suggestions here."
            ),
        )
        self._similar_page()
        if self._gems_available():
            self._simple_page(
                "Hidden gems",
                discovery.hidden_gems(self.items),
                "Well rated but not widely known.",
            )
        self._simple_page(
            "Complete the set",
            discovery.suggestions(self.items, self.owned),
            "Fills genres your collection is still missing.",
            empty_note="No gaps found in the loaded titles.",
        )

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.pack_end(close, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

        self.add(outer)
        self.seed.set_active(0)

    # -- pages ----------------------------------------------------------
    def _gems_available(self) -> bool:
        """Hidden gems needs both a rating and a popularity on some title."""
        return any(
            item.get("rating") is not None and item.get("popularity") is not None
            for item in self.items
        )

    def _list(self, name: str) -> Gtk.ListBox:
        widget = Gtk.ListBox()
        widget.set_selection_mode(Gtk.SelectionMode.NONE)
        widget.set_activate_on_single_click(True)
        widget.connect("row-activated", self._on_row)
        self._lists[name] = widget
        return widget

    def _wrap(self, name: str, widget: Gtk.Widget, note: str) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(widget)
        page.pack_start(scroller, True, True, 0)
        caption = Gtk.Label(label=note, xalign=0)
        caption.set_line_wrap(True)
        caption.set_max_width_chars(70)
        caption.get_style_context().add_class("dim-label")
        page.pack_start(caption, False, False, 0)
        self._notes[name] = caption
        self.notebook.append_page(page, Gtk.Label(label=name))
        return page

    def _simple_page(
        self,
        name: str,
        rows: Sequence[Mapping[str, Any]],
        note: str,
        *,
        empty_note: str | None = None,
    ) -> None:
        widget = self._list(name)
        for item in rows:
            self._add_row(widget, item)
        self._wrap(name, widget, note if rows else (empty_note or note))

    def _similar_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.seed = Gtk.ComboBoxText()
        for item in self.items:
            key = str(item.get("key") or "")
            self.seed.append(key, str(item.get("title") or key))
        self.seed.connect("changed", lambda *_: self.refresh_similar())
        page.pack_start(self.seed, False, False, 0)
        self._similar_list = self._list("More like this")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._similar_list)
        page.pack_start(scroller, True, True, 0)
        caption = Gtk.Label(label="Shares genres or tags with the title above.", xalign=0)
        caption.get_style_context().add_class("dim-label")
        page.pack_start(caption, False, False, 0)
        self._notes["More like this"] = caption
        self.notebook.append_page(page, Gtk.Label(label="More like this"))

    def _add_row(self, listbox: Gtk.ListBox, item: Mapping[str, Any]) -> None:
        row = Gtk.ListBoxRow()
        label = Gtk.Label(label=row_text(item), xalign=0)
        label.set_line_wrap(True)
        label.set_margin_start(6)
        label.set_margin_end(6)
        label.set_margin_top(4)
        label.set_margin_bottom(4)
        row.add(label)
        row.discover_item = dict(item)  # type: ignore[attr-defined]
        row.set_tooltip_text(str(item.get("title") or item.get("key") or ""))
        listbox.add(row)

    # -- behaviour ------------------------------------------------------
    def items_in(self, name: str) -> list[dict[str, Any]]:
        widget = self._lists.get(name)
        if widget is None:
            return []
        return [
            dict(getattr(row, "discover_item", {}))
            for row in widget.get_children()
        ]

    def tab_labels(self) -> list[str]:
        labels: list[str] = []
        for index in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(index)
            labels.append(str(self.notebook.get_tab_label_text(page) or ""))
        return labels

    def note_text(self, name: str) -> str:
        label = self._notes.get(name)
        return str(label.get_text()) if label is not None else ""

    def refresh_similar(self) -> None:
        key = self.seed.get_active_id() or ""
        listbox = self._similar_list
        for row in list(listbox.get_children()):
            listbox.remove(row)
        for item in discovery.similar(self.items, key):
            self._add_row(listbox, item)
        listbox.show_all()

    def _on_row(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        item = getattr(row, "discover_item", None)
        if not item:
            return
        game = self._games.get(str(item.get("key") or ""))
        opener = getattr(self._parent, "_select_game", None)
        if game is not None and callable(opener):
            opener(game)
            self.close()


def show_discover(parent: Any, settings: Any) -> DiscoverWindow:
    """Open Discover from the app menu, fed by the catalogue and the library."""
    rows = game_rows(getattr(parent, "_games", None) or [])
    owned = [str(item["key"]) for item in rows]
    window = DiscoverWindow(
        parent,
        settings,
        rows,
        liked=library_keys(parent),
        owned=owned,
    )
    window.show_all()
    window.present()
    return window


__all__ = ["DiscoverWindow", "game_rows", "library_keys", "row_text", "show_discover"]
