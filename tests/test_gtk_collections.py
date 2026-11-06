"""The GTK collections editor: naming, membership and the empty states.

Skipped without GTK 3 or a display, since the window is made of real widgets.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_collections
from lgl.collections import CollectionStore


def entry(identifier, title):
    return SimpleNamespace(identifier=identifier, title=title)


ENTRIES = [entry("a", "Alpha"), entry("b", "Beta")]


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def window(entries=ENTRIES, store=None):
    return gtk_collections.CollectionsWindow(
        None, store if store is not None else CollectionStore(), entries
    )


def row_titles(listbox):
    return [row.get_child().get_text() for row in listbox.get_children()]


def test_new_collection_starts_empty_and_takes_titles(display):
    win = window()
    try:
        assert win.list.get_children() == []
        assert win.create("Co-op") is True
        assert row_titles(win.list) == ["Co-op (0)"]
        assert win.members_note.get_text() == gtk_collections.NO_MEMBERS
        assert win.add("a") == 1
        assert row_titles(win.members) == ["Alpha"]
        assert win.members.get_row_at_index(0).member_key == "a"
        assert row_titles(win.list) == ["Co-op (1)"]
    finally:
        win.destroy()


def test_renaming_refuses_a_name_already_in_use(display):
    store = CollectionStore()
    win = window(store=store)
    try:
        assert win.create("First") is True
        assert win.create("Second") is True
        assert win.rename("First") is False
        assert win.rename("Third") is True
        assert [collection.name for collection in store.all()] == [
            "First",
            "Third",
        ]
    finally:
        win.destroy()


def test_blank_and_duplicate_names_are_refused(display):
    store = CollectionStore()
    win = window(store=store)
    try:
        assert win.create("") is False
        assert win.create("   ") is False
        assert win.create("Co-op") is True
        assert win.create("cO-op") is False
        assert [collection.name for collection in store.all()] == ["Co-op"]
        assert win.rename("") is False
    finally:
        win.destroy()


def test_removing_a_title_and_deleting_a_collection(display):
    store = CollectionStore()
    win = window(store=store)
    try:
        assert win.create("Keep") is True
        assert win.add("a") == 1
        assert win.add("b") == 1
        assert row_titles(win.members) == ["Alpha", "Beta"]
        assert win.remove("a") == 1
        assert row_titles(win.members) == ["Beta"]
        assert win.delete() is True
        assert win.list.get_children() == []
        assert store.all() == []
    finally:
        win.destroy()


def test_no_collections_says_how_to_make_one(display):
    win = window()
    try:
        assert "No collections yet" in win.collections_note.get_text()
        assert win.rename_button.get_sensitive() is False
        assert win.delete_button.get_sensitive() is False
    finally:
        win.destroy()


def test_an_empty_library_says_why_nothing_can_be_added(display):
    win = window(entries=[])
    try:
        assert win.create("Co-op") is True
        assert win.library_note.get_text() == gtk_collections.NO_LIBRARY
        assert win.members_note.get_text() == gtk_collections.NO_MEMBERS
        assert win.add_button.get_sensitive() is False
        assert win.add("a") == 0
    finally:
        win.destroy()


def test_the_members_note_returns_after_removing_the_last_title(display):
    win = window()
    try:
        win.create("Co-op")
        win.add("a")
        assert win.members_note.get_text() == ""
        win.remove("a")
        assert win.members_note.get_text() == gtk_collections.NO_MEMBERS
        assert win.remove_button.get_sensitive() is False
    finally:
        win.destroy()
