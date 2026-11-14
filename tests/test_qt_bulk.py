"""The Qt bulk bar: check boxes, counts, planned writes and undo."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from lgl import design_components, library_tools, qt_main
from lgl.collections import CollectionStore
from lgl.library_store import LibraryEntry
from lgl.qt_library import LibraryCard, LibraryPage, LibraryRow


class Settings:
    """The settings surface the library page and the tag helpers read."""

    library_games_only = True
    library_show_missing = True
    library_hide_adult = False
    library_group_copies = False

    def __init__(self):
        self.tags: dict[str, list[str]] = {}

    def get(self, key, default=None):
        return self.tags.get(key, default)

    def set(self, key, value):
        self.tags[key] = value

    def save(self):
        return True


class Store:
    """LibraryStore stand-in that records writes and refuses to delete."""

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

    def remove(self, identifier):  # pragma: no cover - must never be called
        raise AssertionError("a bulk action must never remove a library entry")


def pump(rounds: int = 6) -> None:
    for _ in range(rounds):
        QApplication.processEvents()


def settle(page) -> None:
    """Force the polish and layout pass a real window gets before measuring."""
    for card in page._cards.values():
        card.ensurePolished()
        layout = card.layout()
        if layout is not None:
            layout.activate()
    page.ensurePolished()
    pump()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module", autouse=True)
def real_card_style(app):
    """Style the cards the way the app does, at a large font.

    The card padding and border come from the stylesheet, and the rows under
    the artwork are sized from font metrics, so both have to be real here: a
    Windows font metric difference was hiding a card that was too short.
    """
    original_font = app.font()
    font = QFont(original_font)
    font.setPointSize(14)
    app.setFont(font)
    qt_main.apply_theme(app, "dark")
    pump()
    yield
    app.setStyleSheet("")
    app.setFont(original_font)
    pump()


@pytest.fixture
def page(app):
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
    store = Store(entries)
    page = LibraryPage(store, Settings())
    page.set_entries(store.entries())
    page.resize(1100, 720)
    page.show()
    pump()
    page.bulk._collections = CollectionStore()
    yield page
    page.close()
    page.deleteLater()
    pump()


def select(page, *keys):
    bulk = page.bulk
    bulk.toggle.setChecked(True)
    pump()
    for key in keys:
        bulk._checks[key].setChecked(True)
    pump()
    return bulk


def test_select_mode_puts_a_check_box_under_the_artwork_of_every_tile(page):
    bulk = page.bulk
    assert bulk.active is False
    assert bulk.bar.isVisible() is False

    bulk.toggle.setChecked(True)
    pump()

    assert bulk.active is True
    assert bulk.bar.isVisible() is True
    assert len(bulk._checks) == 5
    page.grab()  # a paint pass, as a real window gets
    pump(10)
    settle(page)
    card = page._cards["game-0"]
    check = bulk._checks["game-0"]
    assert isinstance(card, LibraryCard)
    assert check.parentWidget() is card
    # The box sits under the artwork, so it cannot hide its top-left corner.
    assert not check.geometry().intersects(card.cover.geometry())
    assert card.layout().indexOf(card.cover) == 0
    assert card.layout().indexOf(check) == 1
    assert bulk.count.text() == "No games selected"
    assert bulk.tag_button.isEnabled() is False

    bulk._checks["game-2"].setChecked(True)
    pump()
    assert bulk.count.text() == "1 selected"
    assert bulk.keys() == ["game-2"]
    assert bulk.tag_button.isEnabled() is True


def test_a_card_is_tall_enough_for_its_artwork_and_its_text_rows(page):
    """The stylesheet draws padding and a border inside the card, so the
    minimum height has to cover the artwork, the rows and that frame."""
    bulk = select(page, "game-0")
    settle(page)
    card = page._cards["game-0"]
    inset = 2 * (
        design_components.CARD_PADDING + design_components.CARD_BORDER_WIDTH
    )
    rows = card.layout().sizeHint().height() - card.cover.height()

    assert card.minimumHeight() >= card.cover.height() + rows + inset
    assert card.height() >= card.minimumHeight()
    # The last row must sit below the artwork, not inside it.
    check = bulk._checks["game-0"]
    assert check.geometry().top() >= card.cover.geometry().bottom()
    assert not check.geometry().intersects(card.cover.geometry())


def test_the_list_view_gets_check_boxes_in_front_of_the_text(page):
    page._toggle_view()
    pump()
    bulk = select(page, "game-1")

    assert isinstance(page._cards["game-1"], LibraryRow)
    check = bulk._checks["game-1"]
    assert check.parentWidget() is page._cards["game-1"]
    assert check.geometry().left() < page._cards["game-1"].width()

    assert bulk.select_all() == 5
    pump()
    assert bulk.count.text() == "5 selected"
    assert all(entry.isChecked() for entry in bulk._checks.values())

    bulk.clear()
    pump()
    assert bulk.keys() == []
    assert bulk.count.text() == "No games selected"
    assert all(not entry.isChecked() for entry in bulk._checks.values())


def test_leaving_selection_mode_removes_the_check_boxes(page):
    bulk = select(page, "game-0", "game-1")
    assert len(bulk._checks) == 5

    bulk.toggle.setChecked(False)
    pump()

    assert bulk.active is False
    assert bulk.bar.isVisible() is False
    assert bulk._checks == {}
    assert bulk.keys() == []


def test_compact_mode_keeps_the_toggle_the_bar_and_its_check_boxes(page):
    page.set_compact(True)
    pump()
    bulk = select(page, "game-0", "game-1", "game-2")

    assert page._compact is True
    assert bulk.toggle.isVisible() is True
    assert bulk.bar.isVisible() is True
    assert len(bulk._checks) == 5
    assert bulk.count.text() == "3 selected"
    page.grab()
    pump(10)
    settle(page)
    card = page._cards["game-2"]
    assert bulk._checks["game-2"].parentWidget() is card
    assert not bulk._checks["game-2"].geometry().intersects(card.cover.geometry())


def test_tag_action_confirms_reports_and_undoes(page):
    bulk = select(page, "game-0", "game-1", "game-2")
    confirmed = []
    bulk.ask_tag = lambda: ("co-op", False)
    bulk.confirm = lambda text: (confirmed.append(text) or True)

    plan = bulk.run_tag()
    pump()

    assert plan is not None and plan.updated == 3
    assert confirmed == ["Add the tag “co-op” — 3 of 3 games will change."]
    assert bulk.report.text() == "3 updated"
    assert library_tools.tags_for(page.settings, "game-0") == ("co-op",)
    assert library_tools.tags_for(page.settings, "game-3") == ()
    assert bulk.undo_button.isEnabled() is True
    assert page._cards  # the page rebuilt its tiles
    assert bulk.count.text() == "3 selected"

    bulk.undo()
    pump()

    assert library_tools.tags_for(page.settings, "game-0") == ()
    assert bulk.report.text().startswith("Undone")
    assert bulk.undo_button.isEnabled() is False


def test_cancelling_the_confirm_changes_nothing(page):
    bulk = select(page, "game-0")
    bulk.ask_tag = lambda: ("co-op", False)
    bulk.confirm = lambda text: False

    plan = bulk.run_tag()
    pump()

    assert plan is not None and plan.changes
    assert library_tools.tags_for(page.settings, "game-0") == ()
    assert bulk.report.text() == "Cancelled; nothing changed."
    assert bulk.undo_button.isEnabled() is False


def test_favorite_skips_games_that_already_are_favorites(page):
    page.store.get("game-1").favorite = True
    bulk = select(page, "game-0", "game-1", "game-2")
    bulk.confirm = lambda text: True

    plan = bulk.run_favorite(True)
    pump()

    assert plan is not None
    assert plan.summary() == "2 updated, 1 skipped: already a favorite"
    assert bulk.report.text() == "2 updated, 1 skipped: already a favorite"
    assert [entry.favorite for entry in page.store.entries()[:3]] == [True, True, True]


def test_a_bad_year_writes_nothing_and_says_why(page):
    bulk = select(page, "game-0", "game-1", "game-2")
    confirmed = []
    bulk.ask_field = lambda: ("year", "nineteen")
    bulk.confirm = lambda text: (confirmed.append(text) or True)

    plan = bulk.run_field()
    pump()

    assert plan is not None and plan.updated == 0
    assert confirmed == []  # nothing to confirm: no game can change
    assert bulk.report.text() == "0 updated, 3 skipped: year is not a number"
    assert page.store.saved == 0
    assert [entry.year for entry in page.store.entries()[:3]] == ["1998"] * 3


def test_year_edit_and_undo_through_the_store(page):
    bulk = select(page, "game-0", "game-4")
    bulk.ask_field = lambda: ("year", "2001")
    bulk.confirm = lambda text: True

    bulk.run_field()
    pump()
    years = [entry.year for entry in page.store.entries()]
    assert years == ["2001", "1998", "1998", "1998", "2001"]

    bulk.undo()
    pump()
    assert [entry.year for entry in page.store.entries()] == ["1998"] * 5


def test_collection_action_creates_the_collection_and_undo_removes_it_again(page):
    bulk = select(page, "game-0", "game-1")
    bulk.ask_collection = lambda: "Co-op"
    bulk.confirm = lambda text: True

    bulk.run_collection()
    pump()

    store = bulk.collection_store()
    collection = store.by_name("Co-op")
    assert collection is not None
    assert store.members(collection.id) == ["game-0", "game-1"]
    assert bulk.report.text() == "2 updated"

    bulk.undo()
    pump()

    assert store.members(collection.id) == []
    assert store.by_name("Co-op") is None


def test_a_collection_that_already_existed_survives_the_undo(page):
    store = CollectionStore()
    existing = store.create("Co-op")
    store.add(existing.id, ["game-4"])
    page.bulk._collections = store

    bulk = select(page, "game-2")
    bulk.ask_collection = lambda: "Co-op"
    bulk.confirm = lambda text: True
    bulk.run_collection()
    pump()
    assert store.members(existing.id) == ["game-4", "game-2"]

    bulk.undo()
    pump()
    assert store.members(existing.id) == ["game-4"]
    assert store.by_name("Co-op") is not None
