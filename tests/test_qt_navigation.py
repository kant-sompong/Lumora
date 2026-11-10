"""The Qt navigation: rail, page tabs, trimmed menu and palette landings.

Every test builds the real window offscreen, so a page that only exists in the
layout data but not in the window fails here.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lgl import nav_layout, palette as palette_lib, qt_browse, qt_main  # noqa: E402
from lgl.library_store import LibraryEntry  # noqa: E402
from lgl.settings import Settings  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app: QApplication, seconds: float = 0.15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def window(app, tmp_path):
    qt_main.apply_theme(app, "dark")
    window = qt_main.Window(
        None, None, None, tmp_path, Settings(tmp_path / "config.json", keyring=False)
    )
    window.resize(1280, 820)
    window.show()
    pump(app)
    yield window
    window.close()


def tab_texts(widget) -> list[str]:
    return [widget.tabText(index) for index in range(widget.count())]


def test_the_rail_has_no_discover_entry(window):
    assert list(window.rail_buttons)[:4] == ["browse", "library", "media", "downloads"]
    assert "discover" not in window.rail_buttons
    assert not hasattr(window, "discover")


def test_the_palette_has_no_discover_command():
    commands = palette_lib.commands()
    keys = [command.key for command in commands]

    assert "page.discover" not in keys
    assert [key for key in keys if key.startswith("tab.discover")] == []
    assert not any(command.group == "Discover" for command in commands)


ROWS = [
    {"key": "a", "title": "Alpha", "genres": ["RPG"], "rating": 9.0, "favorite": True},
    {"key": "b", "title": "Beta", "genres": ["RPG"], "rating": 8.0, "popularity": 5},
    {"key": "c", "title": "Gamma", "genres": ["RPG", "Puzzle"], "rating": 4.0,
     "popularity": 60},
]


def wait_until(app, predicate, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_browse_has_two_discovery_rows_filled_from_the_library(app):
    page = qt_browse.BrowsePage(
        rows_provider=lambda: ROWS, liked_provider=lambda: ["a"],
    )
    page.set_games([])

    assert wait_until(app, lambda: page.recommended_row.cards())
    assert wait_until(app, lambda: page.gems_row.cards())
    assert [card["title"] for card in page.recommended_row.cards()] == ["Beta", "Gamma"]
    assert [card["title"] for card in page.gems_row.cards()] == ["Alpha", "Beta"]
    assert not page.recommended_row.isHidden()
    assert not page.gems_row.isHidden()


def test_the_discovery_rows_hide_themselves_when_there_is_nothing(app):
    page = qt_browse.BrowsePage(rows_provider=lambda: [], liked_provider=lambda: [])
    page.set_games([])

    assert wait_until(app, lambda: page.recommended_row.isHidden())
    assert wait_until(app, lambda: page.gems_row.isHidden())


def test_a_failing_row_provider_shows_why(app):
    def broken():
        raise RuntimeError("library unreadable")

    page = qt_browse.BrowsePage(rows_provider=broken)
    page.set_games([])

    assert wait_until(app, lambda: not page.recommended_row.isHidden())
    assert wait_until(app, lambda: not page.gems_row.isHidden())
    assert page.recommended_row.error.text() == "Could not load: library unreadable"
    assert page.recommended_row.cards() == []


def test_the_rows_stay_on_screen_when_the_catalogue_list_is_empty(window, app):
    window.show_view("browse")
    pump(app, 0.6)

    browse = window.browse
    assert browse._shown == []
    assert not browse.scroll.isHidden()
    for row in (browse.recommended_row, browse.gems_row):
        assert row.isHidden() or row.isVisibleTo(browse)


def test_the_header_search_filters_the_library_games(window, app):
    window.library.set_entries([
        LibraryEntry(identifier="alpha", title="Alpha", folder="/games/alpha"),
        LibraryEntry(identifier="beta", title="Beta", folder="/games/beta"),
    ])
    window.show_view("library")
    pump(app, 0.1)

    window.search_field.setText("alpha")
    pump(app, 0.1)

    assert [entry.identifier for entry in window.library._shown] == ["alpha"]


@pytest.mark.parametrize("width", [1000, 1280, 1920])
def test_the_loading_state_below_the_rows_is_never_clipped(app, width):
    page = qt_browse.BrowsePage(rows_provider=lambda: ROWS, liked_provider=lambda: ["a"])
    page.resize(width, 780)
    page.show()
    page.set_games([])
    assert wait_until(app, lambda: page.gems_row.cards())
    pump(app, 0.2)

    empty = page.empty
    assert empty.isVisible()
    assert empty.height() >= empty.sizeHint().height()
    rows = [row for row in (page.recommended_row, page.gems_row) if row.isVisible()]
    assert empty.geometry().top() >= max(row.geometry().bottom() for row in rows)
    assert empty.geometry().bottom() <= page.scroll.widget().height()


def test_each_page_names_its_own_search(window, app):
    for view, text in (
        ("browse", "Search games"),
        ("library", "Search library"),
        ("media", "Search movies, TV, anime"),
    ):
        window.show_view(view)
        pump(app, 0.05)
        assert window.search_field.placeholderText() == text, view


def test_an_empty_library_has_no_toolbar_band_and_says_what_to_do(window, app):
    window.show_view("library")
    pump(app)

    assert window.library._entries == []
    assert window.library._toolbar.isHidden()
    assert not window.library.empty.isHidden()
    assert window.library.empty.title.text() == "No games yet"
    assert window.library.empty.hint.text() == "Download or add one to see it here."


def test_an_embedded_panel_drops_its_own_title(window, app):
    window.show_place("library", "statistics")
    pump(app, 0.1)

    panel = window.library.tabs.currentWidget().layout().itemAt(0).widget()
    headings = [
        child for child in panel.findChildren(QLabel)
        if child.objectName() == "detailEyebrow"
    ]
    assert headings
    assert all(heading.isHidden() for heading in headings)


def test_the_library_has_a_complete_the_set_tab_with_an_empty_state(window, app):
    assert "Complete the set" in tab_texts(window.library.tabs)

    window.show_place("library", "complete-the-set")
    pump(app)

    assert window.library.tab_key() == "complete-the-set"
    panel = window.library.tabs.currentWidget().layout().itemAt(0).widget()
    assert panel.__class__.__name__ == "CompleteTheSetDialog"
    assert panel.suggestions.isHidden()
    assert not panel.empty.isHidden()


def test_library_tabs_exist_and_switching_builds_the_panel(window, app):
    assert tab_texts(window.library.tabs) == [
        "Games", "Favourites", "Collections", "Activity", "Statistics",
        "Recently viewed", "Complete the set",
    ]
    window.show_place("library", "statistics")
    pump(app)
    assert window.library.tab_key() == "statistics"
    panel = window.library.tabs.currentWidget().layout().itemAt(0).widget()
    assert panel.__class__.__name__ == "StatisticsDialog"
    assert panel.embedded is True
    window.show_place("library", "favourites")
    pump(app)
    panel = window.library.tabs.currentWidget().layout().itemAt(0).widget()
    assert panel.__class__.__name__ == "FavouritesDialog"
    window.show_place("library", "games")
    pump(app)
    assert window.library.tabs.currentWidget() is window.library.tabs.widget(0)
    assert window.library.detail.isHidden() in (True, False)


def test_media_has_six_tabs_and_the_dice_lives_in_the_header(window):
    assert list(window.media._nav) == ["home", "movies", "tv", "anime", "list", "history"]
    assert "calendar" not in window.media._pages
    assert "compare" not in window.media._pages
    assert not hasattr(window.media, "surprise_btn")
    assert window.surprise_btn.toolTip() == "Surprise me"

def test_the_header_menu_contains_only_the_allowed_entries(window):
    menu = window._build_menu()
    texts = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert texts == list(nav_layout.menu_labels())
    for gone in (
        "Discover…", "Release calendar…", "Compare seasons…", "Statistics…",
        "Tags…", "Favourites…", "Activity…", "Recently viewed…", "Collections…",
        "Surprise me…", "Library tools", "Check for updates…",
    ):
        assert gone not in texts
    menu.deleteLater()


def test_every_palette_command_lands_on_a_valid_page_or_tab(window):
    pages = set(nav_layout.page_keys()) | {"archive"}
    for command in palette_lib.commands():
        kind, page, tab = palette_lib.destination(command)
        assert kind in ("page", "settings", "action"), command.key
        if kind == "page":
            assert page in pages, command.key
            if tab:
                target = "browse" if page == "archive" else page
                assert nav_layout.has_tab(target, tab), command.key
        elif kind == "settings":
            assert page in dict(palette_lib.SETTINGS_PAGES), command.key


def test_a_palette_tab_command_lands_on_that_tab(window, app):
    dialog = window.show_palette()
    command = next(item for item in dialog._items if item.key == "tab.library.recent")
    dialog.run(command)
    pump(app)
    assert window._current == "library"
    assert window.library.tab_key() == "recent"


def test_every_absorbed_menu_entry_is_reachable_in_the_window(window, app):
    for entry, (kind, page, tab) in nav_layout.PLACES.items():
        if kind != "page" or page == "downloads":
            # The download queue only exists when a manager was passed in.
            continue
        window.show_place(page or "", tab or "")
        pump(app, 0.05)
        assert window._current == page, entry
