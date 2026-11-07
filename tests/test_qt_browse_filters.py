"""The Qt Browse filter column: year range, genre include/exclude, summary."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

qt_widgets = pytest.importorskip("PySide6.QtWidgets")

from lgl import browse_filters
from lgl.archive import GameSummary
from lgl.qt_archive import filter_games
from lgl.qt_browse import BrowseFilters

GAMES = [
    GameSummary("a", "Doom", "1993", 10, 1024, subjects=["Action"]),
    GameSummary("b", "Tetris", "1995", 5, 2048, subjects=["Puzzle"]),
    GameSummary("c", "Myst", "1993", 3, 4096, subjects=["Puzzle", "Adventure"]),
    GameSummary("d", "Undated", "", 1, 512, subjects=["Action"]),
]


def checked_pill(group):
    for button in group.buttons():
        if button.isChecked():
            return str(button.property("key"))
    return None


def pill(group, key):
    for button in group.buttons():
        if str(button.property("key")) == key:
            return button
    raise AssertionError(f"no {key!r} pill")


@pytest.fixture(scope="module")
def app():
    return qt_widgets.QApplication.instance() or qt_widgets.QApplication([])


@pytest.fixture
def panel(app):
    widgets = BrowseFilters()
    widgets.set_genres((("Action", 2), ("Puzzle", 2), ("Adventure", 1)), 4)
    widgets.set_fields(browse_filters.available(GAMES))
    return widgets


def test_the_range_and_genres_reach_the_shared_rules():
    assert [
        g.identifier
        for g in filter_games(GAMES, min_year=1992, max_year=1995, genres=["Puzzle"])
    ] == ["b", "c"]
    assert [
        g.identifier
        for g in filter_games(GAMES, genres=["Puzzle"], exclude_genres=["Adventure"])
    ] == ["b"]
    # A title with no year cannot satisfy a range, as content_filters decides.
    assert [g.identifier for g in filter_games(GAMES, max_year=1993)] == ["a", "c"]


def test_the_year_boxes_are_the_filter_and_the_chips_follow(panel):
    panel._from_spin.setValue(1992)
    panel._to_spin.setValue(2005)
    assert (panel.min_year, panel.max_year) == (1992, 2005)
    assert checked_pill(panel._year_group) == "all"

    pill(panel._year_group, "1990s").click()
    assert (panel.min_year, panel.max_year) == (1990, 1999)
    assert (panel._from_spin.value(), panel._to_spin.value()) == (1990, 1999)
    assert checked_pill(panel._year_group) == "1990s"


def test_a_genre_cannot_be_included_and_excluded_at_once(panel):
    panel._toggle_genre("Puzzle", True)
    assert (panel.genres, panel.exclude_genres) == ({"Puzzle"}, set())

    panel._toggle_exclude("Puzzle", True)
    assert (panel.genres, panel.exclude_genres) == (set(), {"Puzzle"})
    assert panel._genre_rows["Puzzle"].isChecked() is False

    panel._toggle_genre("Puzzle", True)
    assert (panel.genres, panel.exclude_genres) == ({"Puzzle"}, set())
    assert panel._exclude_buttons["Puzzle"].isChecked() is False


def test_clear_and_reset_take_every_content_filter_away(panel):
    panel._from_spin.setValue(1990)
    panel._to_spin.setValue(1999)
    panel._toggle_genre("Puzzle", True)
    panel._toggle_exclude("Action", True)

    panel.clear_genres()
    assert (panel.genres, panel.exclude_genres) == (set(), set())
    assert (panel.min_year, panel.max_year) == (1990, 1999)

    panel.reset()
    assert (panel.min_year, panel.max_year) == (0, 0)
    assert (panel._from_spin.value(), panel._to_spin.value()) == (0, 0)


def test_the_summary_line_describes_the_filters(panel):
    panel.update_summary()
    assert panel.summary.text() == ""
    assert panel.summary.isHidden()

    panel._from_spin.setValue(1992)
    panel._to_spin.setValue(2005)
    panel._toggle_genre("Puzzle", True)
    panel._toggle_exclude("Action", True)
    assert panel.summary.text() == "Showing 1992-2005, Puzzle, not Action"
    assert not panel.summary.isHidden()

    panel.reset()
    assert panel.summary.text() == ""
    assert panel.summary.isHidden()


def test_a_section_is_hidden_when_no_title_has_its_field(panel):
    panel.set_fields({"year", "genres"})
    assert panel._genre_block.isVisibleTo(panel)
    assert panel._year_block.isVisibleTo(panel)
    panel.set_fields({"year"})
    assert not panel._genre_block.isVisibleTo(panel)
    assert panel._year_block.isVisibleTo(panel)


def test_the_column_starts_unfiltered(app):
    fresh = BrowseFilters()
    assert fresh.content_filters().is_empty()
    assert (fresh.min_year, fresh.max_year) == (0, 0)
    assert fresh._from_spin.text() == "Any"
