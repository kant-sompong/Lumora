"""The GTK bulk bar: check boxes, counts, writing a plan and undo.

Skipped without GTK 3 or a display, like the rest of the GTK suite.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import library_tools, ui
from lgl.collections import CollectionStore
from lgl.gtk_bulk import BulkActions
from lgl.library_store import LibraryEntry


class Settings(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class Store:
    """The store surface a bulk write needs, without touching a file."""

    def __init__(self, entries):
        self._entries = {entry.identifier: entry for entry in entries}
        self.saved = 0

    def entries(self):
        return list(self._entries.values())

    def get(self, identifier):
        return self._entries.get(identifier)

    def set_favorite(self, identifier, value):
        self._entries[identifier].favorite = bool(value)
        return True

    def save(self):
        self.saved += 1
        return True

    def remove(self, identifier):  # pragma: no cover - never expected
        raise AssertionError("a bulk action must never remove a library entry")


class View:
    """The parts of LibraryView the bar uses, with real tiles."""

    def __init__(self, entries):
        self._entries = list(entries)
        self._store = Store(self._entries)
        self._settings = Settings()
        self._tiles = {}
        self.refreshed = 0
        self.toasts = []
        self.owner = SimpleNamespace(toast=self._toast)
        self.bulk = None

    def _toast(self, text, **kwargs):
        self.toasts.append(text)

    def request_cover(self, path, callback):  # the tiles only need a no-op
        return None

    def refresh(self):
        self.refreshed += 1
        self.bulk.sync()


def pump(rounds=12):
    for _ in range(rounds):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


@pytest.fixture
def built(display):
    entries = [
        LibraryEntry(
            identifier=f"game-{index}",
            title=f"Game {index}",
            year="1998",
            folder=f"/games/{index}",
            size=1024,
        )
        for index in range(5)
    ]
    view = View(entries)
    window = Gtk.Window()
    window.set_default_size(1100, 700)
    flow = Gtk.FlowBox()
    flow.set_homogeneous(True)
    flow.set_max_children_per_line(3)
    for entry in entries:
        tile = ui.LibraryTile(view, entry)
        view._tiles[entry.identifier] = tile
        flow.add(tile)
    window.add(flow)
    window.show_all()
    pump()
    view.bulk = BulkActions(view, collections_store=CollectionStore())
    yield view, window
    window.destroy()


def select(view, *keys):
    bulk = view.bulk
    bulk.toggle.set_active(True)
    pump()
    for key in keys:
        bulk._checks[key].set_active(True)
    pump()
    return bulk


def test_select_mode_puts_one_check_box_on_every_tile(built):
    view, _window = built
    bulk = view.bulk
    assert bulk.active is False
    assert bulk.bar.get_reveal_child() is False

    bulk.toggle.set_active(True)
    pump()

    assert bulk.active is True
    assert bulk.bar.get_reveal_child() is True
    assert len(bulk._checks) == len(view._tiles) == 5
    assert all(check.get_parent() is not None for check in bulk._checks.values())
    assert bulk.count.get_text() == "No games selected"
    assert bulk.tag_button.get_sensitive() is False

    bulk._checks["game-2"].set_active(True)
    pump()
    assert bulk.count.get_text() == "1 selected"
    assert bulk.keys() == ["game-2"]
    assert bulk.tag_button.get_sensitive() is True


def test_select_all_and_clear_drive_the_same_selection(built):
    view, _window = built
    bulk = view.bulk
    bulk.toggle.set_active(True)
    pump()

    assert bulk.select_all() == 5
    assert bulk.count.get_text() == "5 selected"
    assert all(check.get_active() for check in bulk._checks.values())

    bulk.clear()
    pump()
    assert bulk.keys() == []
    assert bulk.count.get_text() == "No games selected"
    assert all(not check.get_active() for check in bulk._checks.values())


def test_leaving_selection_mode_removes_the_check_boxes(built):
    view, _window = built
    bulk = select(view, "game-0", "game-1")
    assert len(bulk._checks) == 5

    bulk.toggle.set_active(False)
    pump()

    assert bulk.active is False
    assert bulk.bar.get_reveal_child() is False
    assert bulk._checks == {}
    assert bulk.keys() == []


def test_tag_action_confirms_reports_and_undoes(built):
    view, _window = built
    bulk = select(view, "game-0", "game-1", "game-2")
    confirmed = []
    bulk.ask_tag = lambda: ("co-op", False)
    bulk.confirm = lambda text: (confirmed.append(text) or True)

    plan = bulk.run_tag()
    pump()

    assert plan is not None and plan.updated == 3
    assert confirmed == ["Add the tag “co-op” — 3 of 3 games will change."]
    assert bulk.report.get_text() == "3 updated"
    assert view.refreshed == 1
    assert view.toasts[-1] == "3 updated"
    assert library_tools.tags_for(view._settings, "game-0") == ("co-op",)
    assert library_tools.tags_for(view._settings, "game-3") == ()
    assert bulk.undo_button.get_sensitive() is True
    # The refresh rebuilt the checks, and the selection is still the same.
    assert len(bulk._checks) == 5
    assert bulk.count.get_text() == "3 selected"

    bulk.undo()
    pump()

    assert library_tools.tags_for(view._settings, "game-0") == ()
    assert bulk.report.get_text().startswith("Undone")
    assert bulk.undo_button.get_sensitive() is False


def test_cancelling_the_confirm_changes_nothing(built):
    view, _window = built
    bulk = select(view, "game-0")
    bulk.ask_tag = lambda: ("co-op", False)
    bulk.confirm = lambda text: False

    plan = bulk.run_tag()
    pump()

    assert plan is not None and plan.changes
    assert library_tools.tags_for(view._settings, "game-0") == ()
    assert bulk.report.get_text() == "Cancelled; nothing changed."
    assert view.refreshed == 0
    assert bulk.undo_button.get_sensitive() is False


def test_favorite_action_skips_games_that_already_are_favorites(built):
    view, _window = built
    view._entries[1].favorite = True
    view._store.get("game-1").favorite = True
    bulk = select(view, "game-0", "game-1", "game-2")
    bulk.confirm = lambda text: True

    plan = bulk.run_favorite(True)
    pump()

    assert plan is not None
    assert plan.summary() == "2 updated, 1 skipped: already a favorite"
    assert bulk.report.get_text() == "2 updated, 1 skipped: already a favorite"
    assert [view._entries[index].favorite for index in range(3)] == [True, True, True]


def test_a_bad_year_writes_nothing_and_says_why(built):
    view, _window = built
    bulk = select(view, "game-0", "game-1", "game-2")
    confirmed = []
    bulk.ask_field = lambda: ("year", "nineteen")
    bulk.confirm = lambda text: (confirmed.append(text) or True)

    plan = bulk.run_field()
    pump()

    assert plan is not None and plan.updated == 0
    assert confirmed == []  # nothing to confirm: no game can change
    assert bulk.report.get_text() == "0 updated, 3 skipped: year is not a number"
    assert view._store.saved == 0
    assert [view._entries[index].year for index in range(3)] == ["1998"] * 3


def test_year_edit_and_undo_through_the_store(built):
    view, _window = built
    bulk = select(view, "game-0", "game-4")
    bulk.ask_field = lambda: ("year", "2001")
    bulk.confirm = lambda text: True

    bulk.run_field()
    pump()
    assert [view._entries[0].year, view._entries[4].year] == ["2001", "2001"]
    assert view._store.saved >= 1

    bulk.undo()
    pump()
    assert [view._entries[0].year, view._entries[4].year] == ["1998", "1998"]


def test_collection_action_creates_the_collection_and_undo_empties_it(built):
    view, _window = built
    bulk = select(view, "game-0", "game-1")
    bulk.ask_collection = lambda: "Co-op"
    bulk.confirm = lambda text: True

    bulk.run_collection()
    pump()

    collection = bulk.collection_store().by_name("Co-op")
    assert collection is not None
    assert bulk.collection_store().members(collection.id) == ["game-0", "game-1"]
    assert bulk.report.get_text() == "2 updated"

    bulk.undo()
    pump()

    assert bulk.collection_store().members(collection.id) == []
    assert bulk.collection_store().by_name("Co-op") is not None
