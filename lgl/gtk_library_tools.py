"""The GTK 3 library tool windows: tags, duplicate groups and the detail editor.

Every rule lives in :mod:`lgl.library_tools`; these windows only collect input
and show what comes back, so they behave like the Qt screens.  GTK 3 only:
``pack_start``, ``.add()`` and ``get_style_context().add_class()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import library_tools

#: Editable fields, in the order the editor shows them.
FIELD_LABELS = (
    ("title", "Title"),
    ("description", "Description"),
    ("year", "Year"),
    ("poster", "Poster"),
)


def as_entry(value: Any) -> dict[str, Any]:
    """A mapping view of a ``LibraryEntry``, a card or a plain dict."""
    if isinstance(value, Mapping):
        data = dict(value)
        data.setdefault("key", data.get("identifier", ""))
        data.setdefault("poster", data.get("cover_path", ""))
        return data
    return {
        "key": getattr(value, "identifier", ""),
        "title": getattr(value, "title", ""),
        "description": getattr(value, "description", ""),
        "year": getattr(value, "year", ""),
        "poster": getattr(value, "cover_path", ""),
        "size": getattr(value, "size", 0),
        "favorite": bool(getattr(value, "favorite", False)),
        "genres": tuple(getattr(value, "genres", ()) or ()),
        "folder": getattr(value, "folder", ""),
    }


def library_entries(parent: Any) -> list[Any]:
    """Everything the library view is showing, in its current order."""
    store = getattr(parent, "library_store", None)
    entries = getattr(store, "entries", None)
    if callable(entries):
        try:
            return list(entries())
        except Exception:  # noqa: BLE001 - a broken index shows an empty window
            return []
    view = getattr(parent, "library_view", None)
    return list(getattr(view, "_entries", None) or [])


def selected_entry(parent: Any) -> Any:
    """The title highlighted in the Library view, or ``None``."""
    view = getattr(parent, "library_view", None)
    for name in ("selected_entry", "_selected_entry"):
        getter = getattr(view, name, None)
        if callable(getter):
            try:
                entry = getter()
            except Exception:  # noqa: BLE001 - no selection is not an error
                return None
            if entry is not None:
                return entry
    return None


def _note(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_line_wrap(True)
    label.set_max_width_chars(70)
    label.get_style_context().add_class("dim-label")
    return label


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.get_style_context().add_class("panel-title")
    return label


def _frame(
    window: Gtk.Window, title: str, width: int, height: int
) -> Gtk.Box:
    window.set_default_size(width, height)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for side in ("top", "bottom", "start", "end"):
        getattr(outer, f"set_margin_{side}")(12)
    window.add(outer)
    return outer


def _scroller(widget: Gtk.Widget, height: int = 0) -> Gtk.ScrolledWindow:
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    if height:
        scroller.set_min_content_height(height)
    scroller.add(widget)
    return scroller


class TagsWindow(Gtk.Window):
    """Add and remove tags on one game, with the library-wide counts below."""

    def __init__(self, parent: Any, settings: Any, entries: Sequence[Any]) -> None:
        super().__init__(title="Tags")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self.settings = settings
        self.entries = [as_entry(entry) for entry in entries]
        outer = _frame(self, "Tags", 480, 460)

        outer.pack_start(_heading("TAGS"), False, False, 0)
        self.picker = Gtk.ComboBoxText()
        for row in self.entries:
            key = str(row.get("key") or "")
            self.picker.append(
                key, str(row.get("title") or key or "Untitled")
            )
        self.picker.connect("changed", lambda *_: self.refresh())
        outer.pack_start(self.picker, False, False, 0)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        outer.pack_start(_scroller(self.list, 200), True, True, 0)

        self.field = Gtk.Entry()
        self.field.set_placeholder_text("Add a tag, e.g. co-op")
        self.field.connect("activate", lambda *_: self.add_current())
        outer.pack_start(self.field, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_button = Gtk.Button(label="Add tag")
        self.add_button.connect("clicked", lambda *_: self.add_current())
        buttons.pack_start(self.add_button, False, False, 0)
        self.remove_button = Gtk.Button(label="Remove tag")
        self.remove_button.connect("clicked", lambda *_: self.remove_current())
        buttons.pack_start(self.remove_button, False, False, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.pack_end(close, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

        self.counts = _note("")
        outer.pack_start(self.counts, False, False, 0)

        self.empty = _note(
            "No games in the library yet, so there are no tags to show."
            if not self.entries
            else "Select a game, then add or remove its tags."
        )
        outer.pack_start(self.empty, False, False, 0)

        can_tag = bool(self.entries)
        for widget in (self.picker, self.field, self.add_button,
                       self.remove_button, self.list):
            widget.set_sensitive(can_tag)
        if can_tag:
            self.picker.set_active(0)
        self.refresh()

    # -- state ---------------------------------------------------------
    def current(self) -> dict[str, Any] | None:
        key = self.picker.get_active_id()
        if key is None:
            return None
        for row in self.entries:
            if str(row.get("key") or "") == key:
                return row
        return None

    def tags(self) -> tuple[str, ...]:
        entry = self.current()
        return library_tools.tags_for(self.settings, entry) if entry else ()

    def refresh(self) -> None:
        for row in list(self.list.get_children()):
            self.list.remove(row)
        for tag in self.tags():
            item = Gtk.ListBoxRow()
            label = Gtk.Label(label=tag, xalign=0)
            label.set_margin_start(4)
            label.set_margin_end(4)
            item.add(label)
            item.tag = tag  # type: ignore[attr-defined]
            self.list.add(item)
        self.list.show_all()
        counts = library_tools.tag_counts(self.entries, self.settings)
        summary = (
            " · ".join(f"{tag} {count}" for tag, count in counts.items())
            if counts
            else "no tags yet"
        )
        self.counts.set_text(f"In this library: {summary}")

    # -- actions -------------------------------------------------------
    def add_current(self) -> None:
        entry = self.current()
        if entry is None:
            return
        text = self.field.get_text()
        if not library_tools.clean_tag(text):
            return
        library_tools.add_tag(self.settings, entry, text)
        self.field.set_text("")
        self.refresh()

    def remove_current(self) -> None:
        entry = self.current()
        row = self.list.get_selected_row()
        if entry is None or row is None:
            return
        library_tools.remove_tag(self.settings, entry, getattr(row, "tag", ""))
        self.refresh()


class DuplicatesWindow(Gtk.Window):
    """Titles that look like the same thing, and where each copy lives."""

    def __init__(self, parent: Any, entries: Sequence[Any]) -> None:
        super().__init__(title="Possible duplicates")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self.groups = library_tools.duplicates(
            [as_entry(entry) for entry in entries]
        )
        outer = _frame(self, "Possible duplicates", 560, 440)

        outer.pack_start(_heading("POSSIBLE DUPLICATES"), False, False, 0)
        outer.pack_start(
            _note(
                "Same title and size. Nothing is deleted here; remove a copy "
                "from its folder if you do not need it."
            ),
            False,
            False,
            0,
        )

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        for group in self.groups:
            self.list.add(self._group_row(group))
        outer.pack_start(_scroller(self.list, 220), True, True, 0)

        self.empty = _note(
            "No duplicates found: every title appears once."
            if not self.groups
            else f"{len(self.groups)} group(s) of copies."
        )
        outer.pack_start(self.empty, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.close())
        buttons.pack_end(close, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

    def _group_row(self, group: Sequence[Mapping[str, Any]]) -> Gtk.ListBoxRow:
        first = group[0]
        title = str(first.get("title") or first.get("key") or "Untitled")
        size = first.get("size")
        detail = f"{title} · {len(group)} copies"
        if size:
            detail += f" · {int(size)} bytes each"
        lines = [detail]
        for row in group:
            where = str(row.get("folder") or row.get("key") or "").strip()
            if where:
                lines.append(f"    {where}")
        item = Gtk.ListBoxRow()
        label = Gtk.Label(label="\n".join(lines), xalign=0)
        label.set_line_wrap(True)
        label.set_margin_start(4)
        label.set_margin_end(4)
        label.set_margin_top(4)
        label.set_margin_bottom(4)
        item.add(label)
        item.group = list(group)  # type: ignore[attr-defined]
        return item


class EditDetailsWindow(Gtk.Window):
    """Correct a title's name, description, year or poster by hand."""

    def __init__(self, parent: Any, entry: Any, store: Any) -> None:
        super().__init__(title="Edit details")
        if isinstance(parent, Gtk.Window):
            self.set_transient_for(parent)
        self.entry = as_entry(entry)
        self.store = store
        self.saved = False
        outer = _frame(self, "Edit details", 520, 460)

        outer.pack_start(_heading("EDIT DETAILS"), False, False, 0)
        outer.pack_start(
            _note(
                "Saved to the library index only; files on disk are not "
                "touched. Blank fields are kept as they are."
            ),
            False,
            False,
            0,
        )

        self.fields: dict[str, Gtk.Widget] = {}
        for name, label in FIELD_LABELS:
            outer.pack_start(_note(label), False, False, 0)
            value = str(self.entry.get(name) or "")
            if name == "description":
                widget: Gtk.Widget = Gtk.TextView()
                widget.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
                widget.get_buffer().set_text(value)
                outer.pack_start(_scroller(widget, 110), True, True, 0)
            else:
                widget = Gtk.Entry()
                widget.set_text(value)
                if name == "year":
                    widget.set_placeholder_text("e.g. 1998")
                elif name == "poster":
                    widget.set_placeholder_text("Path or URL to a poster image")
                outer.pack_start(widget, False, False, 0)
            self.fields[name] = widget

        self.error = _note("")
        outer.pack_start(self.error, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        buttons.pack_end(cancel, False, False, 0)
        self.save_button = Gtk.Button(label="Save")
        self.save_button.connect("clicked", lambda *_: self.save())
        buttons.pack_end(self.save_button, False, False, 0)
        outer.pack_start(buttons, False, False, 0)

    # -- values --------------------------------------------------------
    def values(self) -> dict[str, str]:
        data: dict[str, str] = {}
        for name, widget in self.fields.items():
            if isinstance(widget, Gtk.TextView):
                buffer = widget.get_buffer()
                data[name] = buffer.get_text(
                    buffer.get_start_iter(), buffer.get_end_iter(), True
                )
            else:
                data[name] = widget.get_text()
        return data

    def edited(self) -> dict[str, Any]:
        result = dict(self.entry)
        for name, value in self.values().items():
            result = library_tools.apply_edit(result, name, value)
        return result

    def changed(self) -> bool:
        edited = self.edited()
        return any(
            str(edited.get(name) or "") != str(self.entry.get(name) or "")
            for name, _label in FIELD_LABELS
        )

    def invalid_year(self) -> bool:
        text = self.values()["year"].strip()
        if not text:
            return False
        edited = self.edited()
        return str(edited.get("year")) == str(self.entry.get("year")) and (
            text != str(self.entry.get("year") or "")
        )

    # -- saving --------------------------------------------------------
    def save(self) -> bool:
        self.error.set_text("")
        if self.invalid_year():
            self.error.set_text("Year must be a number; nothing was saved.")
            return False
        values = self.values()
        edited = self.edited()
        if not self.changed():
            self.close()
            return True
        identifier = str(self.entry.get("key") or "")
        if self.store is None:
            self.error.set_text("The library is not available, so nothing was saved.")
            return False
        metadata: dict[str, Any] = {}
        for name in ("title", "description", "year"):
            if values[name].strip():
                metadata[name] = str(edited.get(name) or "")
        poster = values["poster"].strip()
        try:
            if metadata:
                self.store.set_user_metadata(identifier, metadata)
            stored = self.store.get(identifier)
            if poster and stored is not None:
                stored.cover_path = poster
            self.store.save()
        except Exception as exc:  # noqa: BLE001 - reported in the window
            self.error.set_text(f"Could not save: {exc}")
            return False
        self.saved = True
        self.close()
        return True


def show_tags(parent: Any, settings: Any) -> TagsWindow:
    """Open the tag window for the current library."""
    window = TagsWindow(parent, settings, library_entries(parent))
    window.show_all()
    window.present()
    return window


def show_duplicates(parent: Any) -> DuplicatesWindow:
    """Open the duplicate groups found in the current library."""
    window = DuplicatesWindow(parent, library_entries(parent))
    window.show_all()
    window.present()
    return window


def show_edit_details(parent: Any) -> EditDetailsWindow | None:
    """Edit the title selected in the Library view."""
    entry = selected_entry(parent)
    if entry is None:
        toast = getattr(parent, "toast", None)
        if callable(toast):
            toast("Select a game in the Library view first.")
        return None
    store = getattr(parent, "library_store", None)
    window = EditDetailsWindow(parent, entry, store)
    window.show_all()
    window.present()
    return window


__all__ = [
    "DuplicatesWindow",
    "EditDetailsWindow",
    "TagsWindow",
    "as_entry",
    "library_entries",
    "selected_entry",
    "show_duplicates",
    "show_edit_details",
    "show_tags",
]
