"""The GTK 3 shelves editor, mirroring the Qt one.

GTK 3 only: ``pack_start``/``.add``, never GTK 4 widgets or CSS helpers.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import shelves as shelves_lib  # noqa: E402


class ShelvesDialog(Gtk.Dialog):
    """Create a shelf from a genre, list what exists, and remove one."""

    def __init__(self, settings: Any, parent: Gtk.Window | None = None) -> None:
        super().__init__(title="Shelves", transient_for=parent, modal=True)
        self.settings = settings
        self.set_default_size(480, 420)
        self.add_button("Close", Gtk.ResponseType.CLOSE)

        body = self.get_content_area()
        body.set_spacing(8)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_top(12)
        body.set_margin_bottom(12)

        heading = Gtk.Label(label="HOME SHELVES", xalign=0)
        heading.get_style_context().add_class("eyebrow")
        body.pack_start(heading, False, False, 0)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.list)
        scroller.set_size_request(-1, 180)
        body.pack_start(scroller, True, True, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.name = Gtk.Entry()
        self.name.set_placeholder_text("Shelf name, e.g. Co-op night")
        grid.attach(Gtk.Label(label="Name", xalign=0), 0, 0, 1, 1)
        grid.attach(self.name, 1, 0, 1, 1)

        self.genre = Gtk.Entry()
        self.genre.set_placeholder_text("Genre to keep, e.g. RPG (blank keeps everything)")
        grid.attach(Gtk.Label(label="Genre", xalign=0), 0, 1, 1, 1)
        grid.attach(self.genre, 1, 1, 1, 1)

        self.limit = Gtk.SpinButton.new_with_range(1, 40, 1)
        self.limit.set_value(shelves_lib.DEFAULT_LIMIT)
        grid.attach(Gtk.Label(label="Titles shown", xalign=0), 0, 2, 1, 1)
        grid.attach(self.limit, 1, 2, 1, 1)
        body.pack_start(grid, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_btn = Gtk.Button(label="Add shelf")
        self.add_btn.connect("clicked", self._add_clicked)
        buttons.pack_start(self.add_btn, False, False, 0)
        self.remove_btn = Gtk.Button(label="Remove")
        self.remove_btn.connect("clicked", self._remove_clicked)
        buttons.pack_start(self.remove_btn, False, False, 0)
        body.pack_start(buttons, False, False, 0)

        self.note = Gtk.Label(
            label=(
                f"Up to {shelves_lib.MAX_SHELVES} shelves. The row appears above the "
                "Browse grid and is drawn from the loaded catalogue."
            ),
            xalign=0,
        )
        self.note.set_line_wrap(True)
        self.note.set_max_width_chars(60)
        self.note.get_style_context().add_class("dim-label")
        body.pack_start(self.note, False, False, 0)

        self.refresh()
        self.show_all()

    def refresh(self) -> None:
        for row in list(self.list.get_children()):
            self.list.remove(row)
        for shelf in shelves_lib.shelves(self.settings):
            filters = shelf["filters"]
            genres = ", ".join(filters.genres) if filters.genres else "everything"
            row = Gtk.ListBoxRow()
            label = Gtk.Label(
                label=f"{shelf['name']}  ·  {genres}  ·  {shelf['limit']} titles",
                xalign=0,
            )
            label.set_margin_start(6)
            label.set_margin_end(6)
            label.set_margin_top(4)
            label.set_margin_bottom(4)
            row.add(label)
            row.shelf_id = shelf["id"]  # type: ignore[attr-defined]
            self.list.add(row)
        self.remove_btn.set_sensitive(self.list.get_row_at_index(0) is not None)
        self.list.show_all()

    def current_id(self) -> str:
        row = self.list.get_selected_row()
        return str(getattr(row, "shelf_id", "") or "") if row is not None else ""

    def add_shelf(self, name: str, genre: str = "") -> bool:
        text = str(genre or "").strip()
        filters = {"genres": [text]} if text else {}
        key = shelves_lib.add(self.settings, name, filters, int(self.limit.get_value()))
        self.refresh()
        return bool(key)

    def remove_selected(self) -> bool:
        key = self.current_id()
        removed = bool(key) and shelves_lib.remove(self.settings, key)
        self.refresh()
        return removed

    def _add_clicked(self, _button: Gtk.Button) -> None:
        if not self.add_shelf(self.name.get_text(), self.genre.get_text()):
            self.note.set_text(
                "A shelf needs a name, and there is a limit of "
                f"{shelves_lib.MAX_SHELVES} shelves."
            )
            return
        self.name.set_text("")
        self.genre.set_text("")
        self.note.set_text("Shelf added.")

    def _remove_clicked(self, _button: Gtk.Button) -> None:
        if not self.current_id():
            return
        self.remove_selected()


__all__ = ["ShelvesDialog"]
