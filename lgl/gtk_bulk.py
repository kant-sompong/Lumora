"""Bulk selection and actions for the GTK library.

Every rule — what changes, what is skipped and why, and how undo works — lives
in :mod:`lgl.library_tools`.  This module only draws the bar, collects input and
writes the plan through the stores the Library already has, so the Qt screens
can reuse the same logic later.

GTK 3 only: ``pack_start``/``pack_end``, ``.add()``, ``ComboBoxText`` and
``get_style_context().add_class()``.  Nothing here deletes files, moves game
folders, or touches anything but the library index, its tags and the
collections file.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from . import library_tools, platform_support
from .collections import COLLECTIONS_FILENAME, CollectionStore


def _button(label: str, callback: Any, tooltip: str = "") -> Gtk.Button:
    button = Gtk.Button(label=label)
    button.get_style_context().add_class("small-button")
    if tooltip:
        button.set_tooltip_text(tooltip)
    button.connect("clicked", lambda *_: callback())
    return button


class BulkActions:
    """The Select toggle, the "N selected" bar, and the actions it runs."""

    def __init__(self, view: Any, collections_store: Any = None) -> None:
        self.view = view
        self._active = False
        #: Selected identifiers, in the order they were picked.
        self._selected: dict[str, bool] = {}
        self._checks: dict[str, Gtk.CheckButton] = {}
        self._chips: dict[str, Gtk.EventBox] = {}
        self._collections: CollectionStore | None = collections_store
        self.history = library_tools.BulkHistory()

        self.toggle = Gtk.ToggleButton(label="Select")
        self.toggle.set_tooltip_text(
            "Select several games, then tag, collect, favorite or edit them together"
        )
        self.toggle.set_valign(Gtk.Align.CENTER)
        self.toggle.get_accessible().set_name("Select several games")
        self.toggle.connect("toggled", self._on_toggled)

        self.bar = Gtk.Revealer()
        self.bar.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.bar.set_transition_duration(120)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        body.get_style_context().add_class("bulk-bar")
        body.set_margin_start(10)
        body.set_margin_end(10)
        body.set_margin_top(4)
        body.set_margin_bottom(6)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.count = Gtk.Label(label="No games selected", xalign=0)
        self.count.get_style_context().add_class("bulk-count")
        row.pack_start(self.count, False, False, 0)

        self.select_all_button = _button(
            "Select all", self.select_all, "Select every game the grid is showing"
        )
        row.pack_start(self.select_all_button, False, False, 0)

        self.clear_button = _button(
            "Clear", lambda: self.clear(), "Clear the selection"
        )
        row.pack_start(self.clear_button, False, False, 0)

        row.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0
        )

        self.tag_button = _button(
            "Tag…", self.run_tag, "Add or remove one tag on every selected game"
        )
        row.pack_start(self.tag_button, False, False, 0)

        self.collection_button = _button(
            "Collection…",
            self.run_collection,
            "Add every selected game to a collection",
        )
        row.pack_start(self.collection_button, False, False, 0)

        self.favorite_button = _button(
            "Favorite",
            lambda: self.run_favorite(True),
            "Mark every selected game as a favorite",
        )
        row.pack_start(self.favorite_button, False, False, 0)

        self.unfavorite_button = _button(
            "Unfavorite",
            lambda: self.run_favorite(False),
            "Take the favorite mark off every selected game",
        )
        row.pack_start(self.unfavorite_button, False, False, 0)

        self.field_button = _button(
            "Edit field…",
            self.run_field,
            "Set one metadata field (year, genre, developer, publisher) on all of them",
        )
        row.pack_start(self.field_button, False, False, 0)

        self.undo_button = _button(
            "Undo", self.undo, "Put the previous values back for the last bulk action"
        )
        self.undo_button.set_sensitive(False)
        row.pack_start(self.undo_button, False, False, 0)

        self.done_button = _button(
            "Done", lambda: self.toggle.set_active(False), "Leave selection mode"
        )
        row.pack_start(self.done_button, False, False, 0)

        body.pack_start(row, False, False, 0)

        self.report = Gtk.Label(label="", xalign=0)
        self.report.get_style_context().add_class("bulk-report")
        self.report.set_line_wrap(True)
        self.report.set_max_width_chars(110)
        self.report.set_no_show_all(True)
        body.pack_start(self.report, False, False, 0)

        self.bar.add(body)
        self.bar.set_reveal_child(False)

        self._actions = (
            self.tag_button,
            self.collection_button,
            self.favorite_button,
            self.unfavorite_button,
            self.field_button,
        )
        self._sync_controls()

    # -- the widgets the Library already has ---------------------------------
    def _settings(self) -> Any:
        return getattr(self.view, "_settings", None)

    def _store(self) -> Any:
        return getattr(self.view, "_store", None)

    def entries(self) -> list[Any]:
        return list(getattr(self.view, "_entries", None) or [])

    def _tiles(self) -> dict[str, Any]:
        return dict(getattr(self.view, "_tiles", None) or {})

    def collection_store(self) -> CollectionStore:
        if self._collections is None:
            self._collections = CollectionStore(
                platform_support.data_dir() / COLLECTIONS_FILENAME
            )
        return self._collections

    def _window(self) -> Any:
        widget = self.view if isinstance(self.view, Gtk.Widget) else None
        return widget.get_toplevel() if widget is not None else None

    # -- selection -----------------------------------------------------------
    def keys(self) -> list[str]:
        """Selected identifiers, in the order they were picked."""
        return [key for key, on in self._selected.items() if on]

    def is_selected(self, key: Any) -> bool:
        return bool(self._selected.get(str(key or "")))

    def set_selected(self, key: Any, on: bool) -> None:
        text = str(key or "")
        if not text:
            return
        self._selected[text] = bool(on)
        check = self._checks.get(text)
        if check is not None and check.get_active() != bool(on):
            check.set_active(bool(on))
        self._sync_controls()

    def select_all(self) -> int:
        for key in self._tiles():
            self._selected[str(key)] = True
        self.sync()
        self._sync_controls()
        return len(self.keys())

    def clear(self) -> None:
        self._selected.clear()
        for check in self._checks.values():
            check.set_active(False)
        self._sync_controls()

    def _on_check_toggled(self, check: Gtk.CheckButton, key: str) -> None:
        self._selected[key] = bool(check.get_active())
        self._sync_controls()

    # -- selection mode ------------------------------------------------------
    def _on_toggled(self, button: Gtk.ToggleButton) -> None:
        self.set_active(button.get_active())

    def set_active(self, on: bool) -> bool:
        """Turn selection mode on or off; returns whether it is on."""
        self._active = bool(on)
        if self.toggle.get_active() != self._active:
            self.toggle.set_active(self._active)
        self.bar.set_reveal_child(self._active)
        if self._active:
            self.sync()
        else:
            self._clear_checks()
            self._selected.clear()
            self.report.set_text("")
        self._sync_controls()
        return self._active

    @property
    def active(self) -> bool:
        return self._active

    def sync(self) -> int:
        """Put one readable check box inside every tile the grid is showing now.

        The box sits under the artwork, not on it, on a small chip so it stays
        readable over whatever the tile's background is.
        """
        if not self._active:
            return 0
        self._clear_checks()
        for key, tile in self._tiles().items():
            host = self._check_host(tile)
            if host is None:
                continue
            check = Gtk.CheckButton()
            check.set_active(self.is_selected(key))
            check.set_tooltip_text("Select this game")
            check.get_accessible().set_name("Select this game")
            check.connect("toggled", self._on_check_toggled, str(key))
            chip = Gtk.EventBox()
            chip.get_style_context().add_class("tile-check")
            context = tile.get_style_context()
            found, colour = context.lookup_color("theme_bg_color")
            if not found:
                found, colour = context.lookup_color("theme_base_color")
            if found:
                chip.override_background_color(Gtk.StateFlags.NORMAL, colour)
            chip.set_margin_start(4)
            chip.set_margin_end(4)
            chip.set_margin_top(4)
            chip.set_margin_bottom(2)
            chip.set_halign(Gtk.Align.START)
            chip.add(check)
            host.pack_start(chip, False, False, 0)
            chip.show_all()
            self._checks[str(key)] = check
            self._chips[str(key)] = chip
        self._sync_controls()
        return len(self._checks)

    @staticmethod
    def _check_host(tile: Any) -> Gtk.Box | None:
        """The tile's text block: under the artwork, never on top of it."""
        card = tile.get_child() if isinstance(tile, Gtk.Container) else None
        if card is None:
            return None
        for child in card.get_children():
            if isinstance(child, Gtk.Box):
                return child
        return None

    def _clear_checks(self) -> None:
        for chip in self._chips.values():
            parent = chip.get_parent()
            if isinstance(parent, Gtk.Container):
                parent.remove(chip)
        self._checks = {}
        self._chips = {}

    def _sync_checks(self) -> None:
        for key, check in self._checks.items():
            wanted = self.is_selected(key)
            if check.get_active() != wanted:
                check.set_active(wanted)

    def _sync_controls(self) -> None:
        count = len(self.keys())
        self.count.set_text(
            f"{count} selected" if count else "No games selected"
        )
        for widget in self._actions:
            widget.set_sensitive(count > 0)
        self.select_all_button.set_sensitive(bool(self._tiles()))
        self.clear_button.set_sensitive(count > 0)
        self.undo_button.set_sensitive(self.history.can_undo())

    # -- input ---------------------------------------------------------------
    def _dialog(self, title: str, action: str) -> Gtk.Dialog:
        dialog = Gtk.Dialog(title=title, transient_for=self._window(), flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button(action, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        return dialog

    def _intro(self, dialog: Gtk.Dialog, text: str) -> Gtk.Box:
        box = dialog.get_content_area()
        label = Gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("dim-label")
        box.pack_start(label, False, False, 0)
        return box

    def ask_tag(self) -> tuple[str, bool] | None:
        """Ask for a tag and whether to add or remove it."""
        count = len(self.keys())
        dialog = self._dialog("Bulk tag", "Continue")
        box = self._intro(
            dialog,
            f"{count} game{'s' if count != 1 else ''} selected. "
            "One tag is added to all of them, or removed from all of them.",
        )
        mode = Gtk.ComboBoxText()
        mode.append("add", "Add tag")
        mode.append("remove", "Remove tag")
        mode.set_active(0)
        box.pack_start(mode, False, False, 0)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Tag, e.g. co-op")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        tag = entry.get_text()
        remove = mode.get_active_id() == "remove"
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not library_tools.clean_tag(tag):
            return None
        return tag, remove

    def ask_collection(self) -> str | None:
        """Ask which collection the selection joins."""
        store = self.collection_store()
        names = [collection.name for collection in store.all()]
        count = len(self.keys())
        dialog = self._dialog("Bulk collection", "Continue")
        box = self._intro(
            dialog,
            f"Add {count} game{'s' if count != 1 else ''} to a collection. "
            "Type a new name or an existing one.",
        )
        entry = Gtk.Entry()
        entry.set_placeholder_text("Collection name")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        hint = Gtk.Label(
            label=(
                "Existing collections: " + ", ".join(names) if names
                else "There are no collections yet; typing a name makes one."
            ),
            xalign=0,
        )
        hint.get_style_context().add_class("dim-label")
        hint.set_line_wrap(True)
        hint.set_max_width_chars(70)
        box.pack_start(hint, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        name = entry.get_text()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        name = " ".join(name.split())[:80]
        return name or None

    def ask_field(self) -> tuple[str, str] | None:
        """Ask which metadata field to set, and to what."""
        count = len(self.keys())
        dialog = self._dialog("Bulk edit", "Continue")
        box = self._intro(
            dialog,
            f"Set one metadata field on {count} game{'s' if count != 1 else ''}. "
            "Only the library index changes; files on disk are untouched.",
        )
        picker = Gtk.ComboBoxText()
        for name, label in library_tools.BULK_FIELDS:
            picker.append(name, label)
        picker.set_active(0)
        box.pack_start(picker, False, False, 0)
        entry = Gtk.Entry()
        entry.set_placeholder_text("New value, e.g. 1998 or co-op")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        value = entry.get_text()
        field_name = picker.get_active_id() or "year"
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        return field_name, value

    def confirm(self, text: str) -> bool:
        """Ask before writing anything; tests replace this."""
        dialog = Gtk.MessageDialog(
            transient_for=self._window(),
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=text,
        )
        dialog.set_title("Bulk action")
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    # -- actions -------------------------------------------------------------
    def _tags_of(self, entry: Any) -> tuple[str, ...]:
        return library_tools.tags_for(self._settings(), entry)

    def run_tag(self) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        answer = self.ask_tag()
        if answer is None:
            return None
        tag, remove = answer
        plan = library_tools.plan_tags(
            self.entries(), self.keys(), tag, tags_of=self._tags_of, remove=remove
        )
        return self.finish(plan)

    def run_collection(self) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        name = self.ask_collection()
        if not name:
            return None
        store = self.collection_store()
        existing = store.by_name(name)
        members = set(store.members(existing.id)) if existing is not None else set()

        def is_member(entry: Any) -> bool:
            key = str(getattr(entry, "identifier", "") or "")
            return key.strip().casefold() in members

        plan = library_tools.plan_collection(
            self.entries(),
            self.keys(),
            existing.name if existing is not None else name,
            collection_id=existing.id if existing is not None else "",
            is_member=is_member,
        )
        return self.finish(plan)

    def run_favorite(self, value: bool) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        plan = library_tools.plan_favorite(self.entries(), self.keys(), value)
        return self.finish(plan)

    def run_field(self) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        answer = self.ask_field()
        if answer is None:
            return None
        field_name, value = answer
        plan = library_tools.plan_field(self.entries(), self.keys(), field_name, value)
        return self.finish(plan)

    # -- writing -------------------------------------------------------------
    def finish(self, plan: library_tools.BulkPlan) -> library_tools.BulkPlan:
        """Confirm, write, report and remember one plan."""
        if not plan.changes:
            self.show_report(plan.summary() or "Nothing to change.")
            self._sync_controls()
            return plan
        if not self.confirm(plan.confirm()):
            self.show_report("Cancelled; nothing changed.")
            return plan
        written = self.write(plan)
        self.history.remember(written)
        self.show_report(written.summary())
        self._refresh_library()
        self._sync_controls()
        return written

    def write(self, plan: library_tools.BulkPlan) -> library_tools.BulkPlan:
        """Apply one plan, creating the collection it names when needed."""
        if plan.change.action == "collection" and not plan.change.field:
            store = self.collection_store()
            existing = store.by_name(plan.change.value)
            collection = existing if existing is not None else store.create(plan.change.value)
            plan = replace(
                plan,
                change=replace(plan.change, field=collection.id),
                created_collection="" if existing is not None else collection.id,
            )
        library_tools.apply_plan(
            plan,
            settings=self._settings(),
            store=self._store(),
            collections_store=self.collection_store(),
        )
        return plan

    def undo(self) -> library_tools.BulkPlan | None:
        """Put the previous values back for the last bulk action."""
        plan = self.history.undo()
        if plan is None:
            self.show_report("There is no bulk action to undo.")
            self._sync_controls()
            return None
        self.write(plan)
        self.show_report(f"Undone: {plan.updated} game(s) put back.")
        self._refresh_library()
        self._sync_controls()
        return plan

    def _refresh_library(self) -> None:
        refresh = getattr(self.view, "refresh", None)
        if callable(refresh):
            refresh()
        else:
            self.sync()

    def show_report(self, text: str) -> None:
        """Show the result line, and mirror it in the app toast when present."""
        self.report.set_text(text)
        self.report.show()
        owner = getattr(self.view, "owner", None)
        toast = getattr(owner, "toast", None)
        if callable(toast):
            # A missing or broken toast must never fail the write itself.
            with contextlib.suppress(Exception):
                toast(text)


__all__ = ["BulkActions"]
