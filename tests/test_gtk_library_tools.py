"""The GTK library tool windows; skipped without GTK 3 or a display."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_library_tools


class Settings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class FakeStore:
    def __init__(self, entries=()):
        self.updates = []
        self.saved = 0
        self._entries = {entry.identifier: entry for entry in entries}

    def entries(self):
        return list(self._entries.values())

    def get(self, identifier):
        return self._entries.get(identifier)

    def set_user_metadata(self, identifier, values):
        self.updates.append((identifier, dict(values)))
        return self._entries.get(identifier)

    def save(self):
        self.saved += 1
        return True


def game(identifier, title, year="1998", size=100):
    return SimpleNamespace(
        identifier=identifier,
        title=title,
        description="",
        year=year,
        cover_path="",
        size=size,
        favorite=False,
        genres=(),
        folder=f"/games/{identifier}",
    )


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def test_tags_round_trip_and_counts(display):
    settings = Settings()
    window = gtk_library_tools.TagsWindow(
        None, settings, [game("g1", "One"), game("g2", "Two")]
    )
    try:
        window.field.set_text("Co-op")
        window.add_current()
        assert window.tags() == ("co-op",)
        assert "co-op 1" in window.counts.get_text()
        window.list.select_row(window.list.get_row_at_index(0))
        window.remove_current()
        assert window.tags() == ()
        assert "no tags yet" in window.counts.get_text()
    finally:
        window.destroy()


def test_tags_says_why_it_is_empty(display):
    window = gtk_library_tools.TagsWindow(None, Settings(), [])
    try:
        assert "No games in the library" in window.empty.get_text()
        assert not window.add_button.get_sensitive()
    finally:
        window.destroy()


def test_duplicates_groups_copies(display):
    entries = [
        game("g1", "Same"), game("g2", "Same"), game("g3", "Other", size=5)
    ]
    window = gtk_library_tools.DuplicatesWindow(None, entries)
    try:
        assert len(window.groups) == 1
        row = window.list.get_row_at_index(0)
        assert row is not None
        assert len(row.group) == 2
        assert "Same" in window.empty.get_text()
    finally:
        window.destroy()


def test_duplicates_says_when_there_are_none(display):
    window = gtk_library_tools.DuplicatesWindow(
        None, [game("g1", "One"), game("g2", "Two")]
    )
    try:
        assert window.groups == []
        assert "No duplicates found" in window.empty.get_text()
    finally:
        window.destroy()


def test_edit_rejects_a_year_that_is_not_a_number(display):
    store = FakeStore()
    window = gtk_library_tools.EditDetailsWindow(None, game("g1", "One"), store)
    try:
        window.fields["year"].set_text("nineteen")
        assert window.save() is False
        assert "Year must be a number" in window.error.get_text()
        assert store.updates == []
    finally:
        window.destroy()


def test_edit_saves_title_year_and_poster(display):
    entry = game("g1", "One")
    store = FakeStore([entry])
    window = gtk_library_tools.EditDetailsWindow(None, entry, store)
    try:
        window.fields["title"].set_text("New title")
        window.fields["year"].set_text("1999")
        window.fields["poster"].set_text("/covers/new.png")
        assert window.save() is True
        assert store.updates == [("g1", {"title": "New title", "year": "1999"})]
        assert entry.cover_path == "/covers/new.png"
        assert store.saved == 1
    finally:
        window.destroy()


def test_edit_without_a_selection_explains_itself():
    parent = SimpleNamespace(toasts=[])
    parent.toast = lambda message, **kwargs: parent.toasts.append(message)
    assert gtk_library_tools.show_edit_details(parent) is None
    assert parent.toasts == ["Select a game in the Library view first."]
