"""The quick-preview popover on library cards."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lgl import library_tools
from lgl.library_store import LibraryEntry
from lgl.qt_library import LibraryPage


class Settings(dict):
    """Settings surface: dict storage plus the attribute flags the page reads."""

    library_games_only = True
    library_show_missing = True
    library_hide_adult = False
    library_group_copies = False

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class Store:
    def __init__(self, entries):
        self._entries = {entry.identifier: entry for entry in entries}

    def entries(self):
        return list(self._entries.values())

    def get(self, identifier):
        return self._entries.get(identifier)

    def set_favorite(self, identifier, value):
        self._entries[identifier].favorite = bool(value)
        return True

    def save(self):
        return True


def pump(rounds: int = 4) -> None:
    for _ in range(rounds):
        QApplication.processEvents()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_page(app, **flags):
    entries = [
        LibraryEntry(
            identifier=f"game-{index}",
            title=title,
            year=str(1998 + index * 2),
            folder=f"/games/{index}",
            size=size,
            description=(
                "A hand-drawn metroidvania in a ruined insect kingdom.\n"
                "Second line of the blurb with the details.\n"
                "Third line must not appear."
            ),
        )
        for index, (title, size) in enumerate(
            [
                ("Hollow Knight", 1536),
                ("Celeste", 2048),
                ("Braid", 1024),
                ("Inside", 4096),
                ("Super Meat Boy", 3072),
            ]
        )
    ]
    settings = Settings(**flags)
    store = Store(entries)
    page = LibraryPage(store, settings)
    page.set_entries(store.entries())
    page.resize(1280, 820)
    page.show()
    page.grab()  # one paint pass, so the deferred reflow has already run
    pump(10)
    page.preview._delay = 0
    page.preview.selection_changed()
    return page


def test_nothing_shows_when_the_switch_is_off(app):
    page = make_page(app, quick_preview=False)
    assert page.preview.enabled() is False

    page.select("game-0")
    assert page.preview.show_selected() is False
    assert page.preview.isVisible() is False

    card = page._cards["game-0"]
    QApplication.sendEvent(card, QEvent(QEvent.Enter))
    pump()
    assert page.preview.isVisible() is False
    page.close()


def test_the_popover_text_comes_from_the_entry(app):
    page = make_page(app, quick_preview=True)
    library_tools.add_tag(page.settings, "game-0", "co-op")
    assert page.preview.enabled() is True

    page.select("game-0")
    assert page.preview.show_selected() is True
    pump()

    preview = page.preview
    assert preview.isVisible() is True
    assert preview.title.text() == "Hollow Knight"
    assert preview.meta.text() == "1998 · 1.5 KiB"
    assert preview.tags_label.text() == "Tags: co-op"
    assert preview.tags_label.isVisible() is True
    assert preview.description.text() == (
        "A hand-drawn metroidvania in a ruined insect kingdom.\n"
        "Second line of the blurb with the details."
    )
    assert "Third line" not in preview.description.text()
    # It must never take keyboard focus.
    assert preview.focusPolicy() == Qt.NoFocus
    page.close()


def test_it_opens_on_hover_and_closes_on_mouse_leave(app):
    page = make_page(app, quick_preview=True)
    card = page._cards["game-1"]

    QApplication.sendEvent(card, QEvent(QEvent.Enter))
    pump()
    assert page.preview.isVisible() is True
    assert page.preview.title.text() == "Celeste"

    QApplication.sendEvent(card, QEvent(QEvent.Leave))
    pump()
    assert page.preview.isVisible() is False
    page.close()


def test_escape_closes_it(app):
    page = make_page(app, quick_preview=True)
    page.select("game-0")
    assert page.preview.show_selected() is True
    pump()
    assert page.preview.isVisible() is True

    page.activateWindow()
    page.setFocus()
    pump()
    QTest.keyClick(page, Qt.Key_Escape)
    pump()

    assert page.preview.isVisible() is False
    page.close()


def test_a_selection_change_closes_it(app):
    page = make_page(app, quick_preview=True)
    page.select("game-0")
    assert page.preview.show_selected() is True
    pump()

    page.select("game-1")
    pump()

    assert page.preview.isVisible() is False
    page.close()


def test_compact_mode_still_works(app):
    page = make_page(app, quick_preview=True)
    page.set_compact(True)
    pump()

    page.select("game-0")
    assert page.preview.show_selected() is True
    pump()

    assert page._compact is True
    assert page.preview.isVisible() is True
    assert page.preview.title.text() == "Hollow Knight"
    assert page.preview.width() <= 340
    page.close()


def test_a_rebuild_re_anchors_the_preview_to_the_new_tile(app):
    page = make_page(app, quick_preview=True)
    page.select("game-0")
    page.preview.show_selected()
    pump()
    assert page.preview.isVisible() is True

    page.refresh()
    pump()

    assert page.preview.isVisible() is True
    # Re-anchored to a live tile of the current render, never the deleted one.
    assert page.preview._card in page._cards.values()
    assert page.preview.title.text() == "Hollow Knight"
    page.close()


def grid_bounds(page):
    """The grid's own rectangle, computed independently of the module."""
    viewport = page.scroll.viewport()
    return QRect(viewport.mapTo(page, QPoint(0, 0)), viewport.size())


def open_on(page, identifier):
    card = page._cards[identifier]
    QApplication.sendEvent(card, QEvent(QEvent.Enter))
    pump(10)
    page.grab()  # a paint pass, so the deferred re-anchor has run
    pump(10)
    return card


def first_row(page):
    """The tiles of the top row, left to right, as they are laid out now."""
    cards = sorted(
        page._cards.values(),
        key=lambda card: (
            card.mapTo(page, QPoint(0, 0)).y(),
            card.mapTo(page, QPoint(0, 0)).x(),
        ),
    )
    if not cards:
        return []
    top = cards[0].mapTo(page, QPoint(0, 0)).y()
    return [card for card in cards if abs(card.mapTo(page, QPoint(0, 0)).y() - top) < 5]


@pytest.mark.parametrize("spot", ["first", "middle", "last"])
@pytest.mark.parametrize("detail", [True, False], ids=["detail-open", "detail-closed"])
@pytest.mark.parametrize("compact", [False, True], ids=["roomy", "compact"])
def test_the_popover_never_covers_its_own_tile(app, spot, detail, compact):
    page = make_page(app, quick_preview=True)
    page.set_compact(compact)
    pump(10)
    page.grab()
    pump(10)
    if detail:
        page.select(page._shown[4].identifier)
        pump(10)
        page.grab()
        pump(10)
    else:
        page.clear_selection()
        pump(10)

    row = first_row(page)
    picker = {"first": row[0], "middle": row[len(row) // 2], "last": row[-1]}
    tile_card = picker[spot]
    identifier = next(key for key, value in page._cards.items() if value is tile_card)
    card = open_on(page, identifier)
    preview = page.preview
    tile = QRect(card.mapTo(page, QPoint(0, 0)), card.size())

    assert preview.isVisible() is True
    assert not preview.geometry().intersects(tile), preview.geometry().getRect()
    assert grid_bounds(page).contains(preview.geometry())
    page.close()


def test_a_reflow_moves_the_popover_and_keeps_it_off_the_tile(app):
    page = make_page(app, quick_preview=True)
    page.grab()
    pump(10)
    card = open_on(page, "game-1")
    first = page.preview.geometry()
    assert page.preview.isVisible() is True

    page.set_compact(True)  # narrower grid, every tile rebuilt
    pump(10)
    page.grab()
    pump(10)

    card = page._cards["game-1"]
    tile = QRect(card.mapTo(page, QPoint(0, 0)), card.size())
    assert page.preview.isVisible() is True
    assert page.preview.geometry() != first
    assert not page.preview.geometry().intersects(tile)
    assert grid_bounds(page).contains(page.preview.geometry())
    page.close()
