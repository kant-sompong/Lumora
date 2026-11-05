"""The GTK 3 command palette window.

Thin on purpose: the command list, the ranking and the mapping from a command to
a destination all live in :mod:`lgl.palette`, shared with the Qt frontend.  This
module draws the search box and the rows and calls back into the main window.
"""

from __future__ import annotations

from typing import Any

import gi

#: Pin the namespace: GI would hand us Gtk 4 on a system that has both.
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from . import palette as palette_lib

#: Pages that live in the main stack, whichever frontend is running.
_PAGE_KEYS = frozenset(key for key, _label in palette_lib.PAGES)

#: Commands that are really a button inside the Tools window for now.
_TOOLS_ACTIONS = frozenset({"tools.open", "updates.check", "backup.create", "diagnostics.create"})


class PaletteWindow(Gtk.Window):
    """A modal search box over every page, setting and action."""

    def __init__(self, owner: Any = None) -> None:
        super().__init__(title="Command palette")
        self.set_modal(True)
        self.set_default_size(560, 420)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        if isinstance(owner, Gtk.Window):
            self.set_transient_for(owner)
        self._owner = owner
        self._items = palette_lib.commands()
        self._results: list[palette_lib.Command] = []

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_top(14)
        outer.set_margin_bottom(12)
        outer.set_margin_start(14)
        outer.set_margin_end(14)

        self._entry = Gtk.SearchEntry()
        self._entry.set_placeholder_text("Type a page, setting or command")
        self._entry.connect("search-changed", self._on_search)
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("key-press-event", self._on_entry_key)
        outer.pack_start(self._entry, False, False, 0)

        self._list = Gtk.ListBox()
        self._list.set_activate_on_single_click(True)
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(-1, 300)
        scroller.add(self._list)
        outer.pack_start(scroller, True, True, 0)

        self._empty = Gtk.Label(label="Nothing matches that.")
        self._empty.set_xalign(0)
        self._empty.get_style_context().add_class("dim-label")
        self._empty.set_no_show_all(True)
        outer.pack_start(self._empty, False, False, 0)

        hint = Gtk.Label(label="Enter runs the highlighted command · Escape closes")
        hint.set_xalign(0)
        hint.get_style_context().add_class("dim-label")
        outer.pack_start(hint, False, False, 0)

        self.add(outer)
        self.connect("key-press-event", self._on_key)
        self.show_all()
        self._fill("")
        self._entry.grab_focus()

    # -- results ------------------------------------------------------------
    def _fill(self, text: str) -> None:
        for child in self._list.get_children():
            self._list.remove(child)
        self._results = palette_lib.search(text, items=self._items)
        for command in self._results:
            row = Gtk.ListBoxRow()
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title = Gtk.Label(label=command.title, xalign=0)
            title.set_hexpand(True)
            line.pack_start(title, True, True, 0)
            if command.group:
                group = Gtk.Label(label=command.group, xalign=1)
                group.get_style_context().add_class("dim-label")
                line.pack_end(group, False, False, 0)
            row.add(line)
            self._list.add(row)
        self._list.show_all()
        if self._results:
            self._list.select_row(self._list.get_row_at_index(0))
            self._empty.hide()
        else:
            self._empty.show()

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self._fill(entry.get_text())

    def _on_entry_key(self, _entry: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            rows = self._list.get_children()
            if not rows:
                return False
            selected = self._list.get_selected_row()
            index = rows.index(selected) if selected is not None else -1
            step = 1 if event.keyval == Gdk.KEY_Down else -1
            next_index = max(0, min(len(rows) - 1, index + step))
            self._list.select_row(rows[next_index])
            return True
        return False

    def _on_entry_activate(self, _entry: Gtk.SearchEntry) -> None:
        self._run_selected()

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if 0 <= index < len(self._results):
            self._run(self._results[index])

    def _run_selected(self) -> None:
        selected = self._list.get_selected_row()
        index = selected.get_index() if selected is not None else -1
        if 0 <= index < len(self._results):
            self._run(self._results[index])

    def _on_key(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False

    # -- dispatch -----------------------------------------------------------
    def _run(self, command: palette_lib.Command) -> None:
        action = palette_lib.action_for(command) or ""
        owner = self._owner
        self.destroy()
        if action in _PAGE_KEYS:
            apply_view = getattr(owner, "_apply_view", None)
            if callable(apply_view):
                apply_view(action)
            return
        if action.startswith("settings."):
            show_settings = getattr(owner, "show_settings", None)
            if callable(show_settings):
                show_settings(page=action.split(".", 1)[1])
            return
        method_name = "focus_search" if action == "search.focus" else None
        if action in _TOOLS_ACTIONS:
            method_name = "show_tools"
        method = getattr(owner, method_name, None) if method_name else None
        if callable(method):
            method()


def show_palette(owner: Any = None) -> PaletteWindow:
    """Open the palette over ``owner`` and keep it until a command runs."""
    window = PaletteWindow(owner)
    window.present()
    return window


__all__ = ["PaletteWindow", "show_palette"]
