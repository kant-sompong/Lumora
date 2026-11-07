"""The Qt home shelves take their size from the Home layout section."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from lgl import home_layout, qt_browse, shelves  # noqa: E402
from lgl.archive import GameSummary  # noqa: E402


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def game(title, subjects=("RPG",)):
    return GameSummary(f"id-{title}", title, "2004", 0, None, subjects=list(subjects))


def pills(page):
    return page.shelves_host.findChildren(QPushButton)


def build(settings):
    page = qt_browse.BrowsePage(settings=settings)
    page.set_games([game("Keroro"), game("Other")])
    return page


def test_a_section_size_changes_the_shelf_row(app):
    settings = Store()
    shelf = shelves.add(settings, "Co-op night", {"genres": ["rpg"], "limit": 8})
    assert shelf

    home_layout.HomeLayout(settings).set_size("continue", "compact")
    small = build(settings)
    small_heights = [pill.maximumHeight() for pill in pills(small)]

    home_layout.HomeLayout(settings).set_size("continue", "large")
    large = build(settings)
    large_heights = [pill.maximumHeight() for pill in pills(large)]

    assert small_heights and large_heights
    assert max(small_heights) < max(large_heights)
    assert max(large_heights) == home_layout.size_height("large")


def test_a_compact_section_shows_fewer_titles_than_a_large_one(app):
    settings = Store()
    shelves.add(settings, "Co-op night", {"genres": ["rpg"]}, 12)
    games = [game(f"RPG {index}") for index in range(8)]

    home_layout.HomeLayout(settings).set_size("continue", "compact")
    compact = qt_browse.BrowsePage(settings=settings)
    compact.set_games(games)

    home_layout.HomeLayout(settings).set_size("continue", "large")
    large = qt_browse.BrowsePage(settings=settings)
    large.set_games(games)

    assert len(pills(compact)) == home_layout.shelf_titles("compact")
    assert len(pills(large)) == home_layout.shelf_titles("large")
    assert len(pills(compact)) < len(pills(large))


def test_the_size_applies_live_without_a_restart(app):
    settings = Store()
    shelves.add(settings, "Co-op night", {"genres": ["rpg"]})
    page = build(settings)
    assert max(pill.maximumHeight() for pill in pills(page)) != home_layout.size_height("large")

    home_layout.HomeLayout(settings).set_size("continue", "large")
    page.refresh_home_layout()

    assert max(pill.maximumHeight() for pill in pills(page)) == home_layout.size_height("large")
