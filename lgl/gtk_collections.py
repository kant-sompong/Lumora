"""The GTK 3 collections editor: name a collection, then put library titles in
or take them out.  Membership rules stay in :mod:`lgl.collections`; this window
only gathers rows and draws them.  GTK 3 only: ``pack_start``, ``.add()`` and
``get_style_context().add_class()``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import platform_support
from .collections import COLLECTIONS_FILENAME, CollectionStore

#: Shown under the title column when a collection has no titles yet.
NO_MEMBERS = (
    "This collection has no titles yet. Choose one below and press Add title."
)

#: Shown when the library itself has nothing to add.
NO_LIBRARY = "The library is empty, so there are no titles to add yet."

#: Shown when no collection is selected.
NO_SELECTION = "Select a collection to see its titles."

#: Smart collections are filters, not membership lists; say so plainly.
SMART_NOTE = (
    "This is a smart collection: it is built from filters, not from titles you "
    "add. Smart collections are out of scope here, so membership stays manual."
)


def title_of(entry: Any) -> str:
    """The name to show for a library entry."""
    return str(
        getattr(entry, "title", "")
        or getattr(entry, "identifier", "")
        or "Untitled"
    )


def key_of(entry: Any) -> str:
    """The identifier a collection actually stores."""
    return str(getattr(entry, "identifier", "") or "")


def library_entries(parent: Any) -> list[Any]:
    """Everything the library index holds."""
    store = getattr(parent, "library_store", None)
    entries = getattr(store, "entries", None)
    if callable(entries):
        try:
            return list(entries())
        except Exception:  # noqa: BLE001 - a broken index shows an empty window
            return []
    view = getattr(parent, "library_view", None)
    return list(getattr(view, "_entries", None) or [])


def _note(text: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_max_width_chars(56)
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


def _row(text: str) -> Gtk.ListBoxRow:
    item = Gtk.ListBoxRow()
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_margin_start(4)
    label.set_margin_end(4)
    label.set_margin_top(2)
    label.set_margin_bottom(2)
    item.add(label)
    return item


class CollectionsWindow(Gtk.Window):
    """The collection list on the left, the chosen collection's titles on the right."""

    def __init__(
        self, parent: Any, store: CollectionStore, entries: Iterable[Any]
    ) -> None:
        super().__init__(title="Collections")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self.store = store
        self.entries = list(entries)
        outer = _frame(self, 680, 480)
        outer.pack_start(_heading("COLLECTIONS"), False, False, 0)
        outer.pack_start(
            _note(
                "Collections are stored beside the library and never move or "
                "delete files. Membership is added by hand."
            ),
            False,
            False,
            0,
        )

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        columns.set_homogeneous(True)
        outer.pack_start(columns, True, True, 0)
        self.columns = columns
        columns.pack_start(self._collection_column(), True, True, 0)
        columns.pack_start(self._title_column(), True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.pack_end(close, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

        self.refresh()

    # -- layout ---------------------------------------------------------
    def _collection_column(self) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.collections_heading = _heading("")
        column.pack_start(self.collections_heading, False, False, 0)
        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", lambda *_: self.refresh_members())
        column.pack_start(_scroller(self.list), True, True, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.new_button = Gtk.Button(label="New…")
        self.new_button.connect("clicked", lambda *_: self._new_clicked())
        row.pack_start(self.new_button, False, False, 0)
        self.rename_button = Gtk.Button(label="Rename…")
        self.rename_button.connect("clicked", lambda *_: self._rename_clicked())
        row.pack_start(self.rename_button, False, False, 0)
        self.delete_button = Gtk.Button(label="Delete")
        self.delete_button.connect("clicked", lambda *_: self._delete_clicked())
        row.pack_start(self.delete_button, False, False, 0)
        column.pack_start(row, False, False, 0)

        self.collections_note = _note()
        column.pack_start(self.collections_note, False, False, 0)
        return column

    def _title_column(self) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.titles_heading = _heading("")
        column.pack_start(self.titles_heading, False, False, 0)
        self.members = Gtk.ListBox()
        self.members.set_selection_mode(Gtk.SelectionMode.SINGLE)
        column.pack_start(_scroller(self.members), True, True, 0)

        self.picker = Gtk.ComboBoxText()
        for entry in self.entries:
            self.picker.append(key_of(entry), title_of(entry))
        column.pack_start(self.picker, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_button = Gtk.Button(label="Add title")
        self.add_button.connect("clicked", lambda *_: self._add_clicked())
        row.pack_start(self.add_button, False, False, 0)
        self.remove_button = Gtk.Button(label="Remove")
        self.remove_button.connect("clicked", lambda *_: self._remove_clicked())
        row.pack_start(self.remove_button, False, False, 0)
        column.pack_start(row, False, False, 0)

        self.members_note = _note(NO_SELECTION)
        column.pack_start(self.members_note, False, False, 0)

        self.library_note = _note("" if self.entries else NO_LIBRARY)
        column.pack_start(self.library_note, False, False, 0)
        return column

    # -- state ---------------------------------------------------------
    def current_id(self) -> str:
        row = self.list.get_selected_row()
        return str(getattr(row, "collection_id", "") or "") if row else ""

    def current_member(self) -> str:
        row = self.members.get_selected_row()
        return str(getattr(row, "member_key", "") or "") if row else ""

    def refresh(self) -> None:
        """Rebuild the collection list, keeping the selection when possible."""
        wanted = self.current_id()
        for row in list(self.list.get_children()):
            self.list.remove(row)
        collections = self.store.all()
        for collection in collections:
            item = _row(f"{collection.name} ({len(collection.items)})")
            item.collection_id = collection.id  # type: ignore[attr-defined]
            self.list.add(item)
        self.list.show_all()
        self.collections_heading.set_text(f"COLLECTIONS ({len(collections)})")
        self.collections_note.set_text(
            "" if collections else "No collections yet. Press New… to make one."
        )
        target = next(
            (row for row in self.list.get_children()
             if getattr(row, "collection_id", "") == wanted),
            None,
        )
        if target is None and self.list.get_children():
            target = self.list.get_row_at_index(0)
        if target is not None:
            self.list.select_row(target)
        self.new_button.set_sensitive(True)
        has_collection = bool(self.list.get_children())
        self.rename_button.set_sensitive(has_collection)
        self.delete_button.set_sensitive(has_collection)
        self.refresh_members()

    def refresh_members(self) -> None:
        for row in list(self.members.get_children()):
            self.members.remove(row)
        collection = self.store.get(self.current_id())
        if collection is None:
            self.titles_heading.set_text("TITLES")
            self.members_note.set_text(NO_SELECTION)
            self.add_button.set_sensitive(False)
            self.remove_button.set_sensitive(False)
            return
        if collection.smart:
            self.titles_heading.set_text(f"{collection.name.upper()}")
            self.members_note.set_text(SMART_NOTE)
            self.add_button.set_sensitive(False)
            self.remove_button.set_sensitive(False)
            return
        rows = self.store.resolve(collection.id, self.entries)
        for entry in rows:
            item = _row(title_of(entry))
            item.member_key = key_of(entry)  # type: ignore[attr-defined]
            self.members.add(item)
        self.members.show_all()
        self.titles_heading.set_text(
            f"{collection.name.upper()} ({len(rows)})"
        )
        self.members_note.set_text("" if rows else NO_MEMBERS)
        self.add_button.set_sensitive(
            bool(self.entries) and collection.id != ""
        )
        self.remove_button.set_sensitive(bool(rows))

    # -- actions (also used by the tests) ------------------------------
    def create(self, name: str) -> bool:
        try:
            collection = self.store.create(name)
        except ValueError:
            return False
        self.refresh()
        for row in self.list.get_children():
            if getattr(row, "collection_id", "") == collection.id:
                self.list.select_row(row)
                break
        return True

    def rename(self, name: str) -> bool:
        collection_id = self.current_id()
        if not collection_id:
            return False
        try:
            changed = self.store.rename(collection_id, name)
        except ValueError:
            return False
        self.refresh()
        return changed

    def delete(self) -> bool:
        collection_id = self.current_id()
        if not collection_id:
            return False
        removed = self.store.delete(collection_id)
        self.refresh()
        return removed

    def add(self, key: str) -> int:
        collection_id = self.current_id()
        collection = self.store.get(collection_id)
        if collection is None or collection.smart:
            return 0
        added = self.store.add(collection_id, [key])
        self.refresh()
        return added

    def remove(self, key: str) -> int:
        collection_id = self.current_id()
        collection = self.store.get(collection_id)
        if collection is None or collection.smart:
            return 0
        removed = self.store.remove(collection_id, [key])
        self.refresh()
        return removed

    # -- buttons -------------------------------------------------------
    def _new_clicked(self) -> None:
        name = self._ask_name("New collection")
        if name is not None and not self.create(name):
            self._warn("That name is empty or already used.")

    def _rename_clicked(self) -> None:
        collection = self.store.get(self.current_id())
        if collection is None:
            return
        name = self._ask_name("Rename collection", collection.name)
        if name is not None and not self.rename(name):
            self._warn("That name is empty or already used.")

    def _delete_clicked(self) -> None:
        collection = self.store.get(self.current_id())
        if collection is None:
            return
        if self._confirm(
            f"Delete {collection.name!r}? The titles themselves are kept."
        ):
            self.delete()

    def _add_clicked(self) -> None:
        self.add(str(self.picker.get_active_id() or ""))

    def _remove_clicked(self) -> None:
        key = self.current_member()
        if key:
            self.remove(key)

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(initial)
        entry.set_activates_default(True)
        box = dialog.get_content_area()
        box.set_spacing(6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.pack_start(Gtk.Label(label="Name", xalign=0), False, False, 0)
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        return name

    def _confirm(self, text: str) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=text,
        )
        dialog.set_title("Delete collection")
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES

    def _warn(self, text: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CLOSE,
            text=text,
        )
        dialog.run()
        dialog.destroy()


def show_collections(parent: Any) -> CollectionsWindow:
    """Open Collections, stored beside the library like the Qt build."""
    store = CollectionStore(
        platform_support.data_dir() / COLLECTIONS_FILENAME
    )
    window = CollectionsWindow(parent, store, library_entries(parent))
    window.show_all()
    window.present()
    return window


__all__ = [
    "CollectionsWindow",
    "key_of",
    "library_entries",
    "show_collections",
    "title_of",
]
