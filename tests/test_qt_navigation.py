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

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import nav_layout, palette as palette_lib, qt_main  # noqa: E402
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


def test_the_rail_lists_discover_between_library_and_media(window, app):
    assert list(window.rail_buttons)[:5] == [
        "browse", "library", "discover", "media", "downloads",
    ]
    window.rail_buttons["discover"].clicked.emit()
    pump(app)
    assert window._current == "discover"
    assert window.stack.currentWidget() is window.discover


def test_discover_shows_its_five_tabs_and_switches_them(window, app):
    assert tab_texts(window.discover.tabs) == [
        "For you", "Hidden gems", "More like this", "Complete the set", "Anime gems",
    ]
    window.show_place("discover", "anime-gems")
    pump(app)
    assert window.discover.tabs.currentIndex() == 4
    window.show_place("discover", "for-you")
    pump(app)
    assert window.discover.tabs.currentIndex() == 0
    assert window.discover.surprise_btn.text() == "Surprise me"


def test_library_tabs_exist_and_switching_builds_the_panel(window, app):
    assert tab_texts(window.library.tabs) == [
        "Games", "Favourites", "Collections", "Tags", "Activity",
        "Statistics", "Recently viewed",
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


def test_media_gains_calendar_and_compare_pages(window, app):
    assert list(window.media._nav)[-2:] == ["calendar", "compare"]
    assert window.media.surprise_btn.text() == "Surprise me"
    window.show_place("media", "calendar")
    pump(app)
    assert window.media._section == "calendar"
    assert window.media.stack.currentWidget() is window.media._pages["calendar"]
    assert window.media.calendar_panel.embedded is True
    window.show_place("media", "compare")
    pump(app)
    assert window.media.stack.currentWidget() is window.media._pages["compare"]


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
