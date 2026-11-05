"""The GTK "Recently viewed" window, reading the shared history.

The list itself lives in :mod:`lgl.recently_viewed` and is written by the Qt
frontend and by this one alike, so both desktops show the same titles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import recently_viewed  # noqa: E402

#: How each stored kind is shown next to the title.
KIND_NAMES = {"game": "Game", "movie": "Movie", "tv": "TV", "anime": "Anime"}


class RecentlyViewedWindow(Gtk.Window):
    """Rows of opened titles; activating one asks the app to go there."""

    def __init__(
        self,
        parent: Gtk.Window | None,
        path: Path,
        on_open: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(title="Recently viewed")
        self.set_default_size(560, 520)
        if parent is not None:
            self.set_transient_for(parent)
        self._path = Path(path)
        self._on_open = on_open

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(12)

        head = Gtk.Label(label="Recently viewed", xalign=0)
        head.get_style_context().add_class("panel-title")
        outer.pack_start(head, False, False, 0)

        self._note = Gtk.Label(xalign=0)
        self._note.set_line_wrap(True)
        self._note.get_style_context().add_class("dim-label")
        outer.pack_start(self._note, False, False, 0)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.set_activate_on_single_click(False)
        self._list.connect("row-activated", self._on_row)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        outer.pack_start(scroller, True, True, 0)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        clear = Gtk.Button(label="Clear history")
        clear.connect("clicked", self._clear)
        bar.pack_start(clear, False, False, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.destroy())
        bar.pack_end(close, False, False, 0)
        outer.pack_start(bar, False, False, 0)

        self.add(outer)
        self.refresh()

    def refresh(self) -> None:
        for row in self._list.get_children():
            self._list.remove(row)
        entries = recently_viewed.recent(self._path, recently_viewed.MAX_ENTRIES)
        for entry in entries:
            row = Gtk.ListBoxRow()
            row.entry_data = (entry["kind"], entry["key"])  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(8)
            box.set_margin_end(8)
            kind = KIND_NAMES.get(entry["kind"], entry["kind"].title())
            title = Gtk.Label(label=f"{entry['title']}  ·  {kind}", xalign=0)
            box.pack_start(title, False, False, 0)
            if entry["subtitle"]:
                subtitle = Gtk.Label(label=entry["subtitle"], xalign=0)
                subtitle.get_style_context().add_class("dim-label")
                subtitle.set_line_wrap(True)
                box.pack_start(subtitle, False, False, 0)
            row.add(box)
            row.set_activatable(self._on_open is not None)
            self._list.add(row)
        self._list.show_all()
        self._note.set_text(
            ""
            if entries
            else "Nothing yet. Open a game, movie, series or anime and it appears here."
        )

    def _on_row(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if self._on_open is None:
            return
        data = getattr(row, "entry_data", None)
        if not data:
            return
        kind, key = data
        self._on_open(str(kind), str(key))
        self.destroy()

    def _clear(self, *_args: Any) -> None:
        recently_viewed.clear(self._path)
        self.refresh()


def show_recent(
    parent: Gtk.Window | None,
    settings: Any = None,
    on_open: Callable[[str, str], None] | None = None,
) -> RecentlyViewedWindow:
    """Open the window and return it (the caller keeps the reference)."""
    window = RecentlyViewedWindow(parent, recently_viewed.default_path(), on_open)
    window.show_all()
    return window


__all__ = ["KIND_NAMES", "RecentlyViewedWindow", "show_recent"]
