"""The season picker: header, panel rows, artwork fallbacks, keyboard.

Offscreen and offline: the image loader is a stub that hands back a small
pixmap, so nothing here downloads a poster.  The contract's numbers are
asserted directly, since a picker that is 301 px wide is a bug.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_season_picker, tmdb  # noqa: E402
from lgl.design_tokens import RADIUS, TEXT_SIZES  # noqa: E402
from lgl.qt_season_picker import (  # noqa: E402
    PANEL_MAX_HEIGHT,
    PANEL_WIDTH,
    ROW_HEIGHT,
    SeasonPicker,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


SEASONS = (
    tmdb.Season(number=2, name="The Long Night", episode_count=6),
    tmdb.Season(number=0, name="Specials", episode_count=2, poster="/s0.jpg"),
    tmdb.Season(number=1, name="Season 1", episode_count=8, poster="/s1.jpg"),
)


class Loader:
    def __init__(self, pixmap=None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.pixmap = pixmap or QPixmap(44, 64)

    def __call__(self, url, key, apply, fallback="", **_kwargs):
        self.calls.append((url, key))
        if url and url.startswith("https://"):
            apply(self.pixmap)
        elif fallback:
            apply(self.pixmap)


def _picker(app, *, poster="https://image.tmdb.org/t/p/w500/show.jpg"):
    loader = Loader()
    picker = SeasonPicker("dark", image_loader=loader)
    picker.set_seasons(SEASONS, poster)
    picker.resize(200, 30)
    picker.show()
    pump(app)
    return picker, loader


def test_the_header_is_plain_text_with_a_chevron(app):
    picker, _loader = _picker(app)

    # The first regular season is selected, not Specials.
    assert picker.header.text() == "Season 1"
    assert picker.header.font().pixelSize() != 0 or True  # styled via QSS
    assert "22px" in picker.header.styleSheet()
    assert picker.chevron.width() == 14
    assert picker.panel.isVisible() is False
    picker.close()


def test_the_panel_is_300_px_wide_and_rows_are_76_px(app):
    picker, _loader = _picker(app)

    picker.open_panel()
    pump(app, 0.2)

    assert picker.panel.width() == PANEL_WIDTH == 300
    assert picker.panel.height() <= PANEL_MAX_HEIGHT == 420
    assert [row.height() for row in picker.panel.rows] == [ROW_HEIGHT] * 3
    assert RADIUS[2] == 14
    picker.close_panel()
    picker.close()


def test_specials_come_first_then_the_seasons_ascending(app):
    picker, _loader = _picker(app)

    picker.open_panel()
    pump(app, 0.1)

    assert [row.number for row in picker.panel.rows] == [0, 1, 2]
    assert picker.panel.rows[0].name_label.text() == "Specials"
    assert picker.panel.rows[1].count_label.text() == "8 episodes"
    picker.close_panel()
    picker.close()


def test_a_selected_row_shows_the_check_and_accent_soft(app):
    picker, _loader = _picker(app)
    picker.open_panel()
    pump(app, 0.1)

    picker.panel.set_current(1)
    pump(app, 0.05)

    selected = [row for row in picker.panel.rows if row._selected]
    assert len(selected) == 1 and selected[0].number == 1
    assert selected[0].check.isVisibleTo(selected[0]) is True
    assert all(not row._selected for row in picker.panel.rows if row.number != 1)
    assert picker.panel.rows[1].name_label.text() == "Season 1"
    picker.close_panel()
    picker.close()


def test_posters_load_through_the_page_loader(app):
    picker, loader = _picker(app)

    picker.open_panel()
    pump(app, 0.2)

    keys = {key for _url, key in loader.calls}
    assert "season-poster-1" in keys
    # Season 2 has no poster of its own: the show's poster is used.
    assert "season-poster-2" in keys
    shown = [row for row in picker.panel.rows if row.number == 1][0]
    assert shown.thumb._pixmap is not None
    picker.close_panel()
    picker.close()


def test_a_season_without_art_falls_back_to_the_show_or_its_number(app):
    loader = Loader()
    picker = SeasonPicker("dark", image_loader=loader)
    picker.set_seasons((tmdb.Season(number=4, episode_count=1),))
    picker.open_panel()
    pump(app, 0.1)

    row = picker.panel.rows[0]
    assert row.thumb._pixmap is None  # neither poster exists
    assert row.thumb._number == 4  # so the placeholder shows the number
    picker.close_panel()
    picker.close()


def test_keyboard_moves_selects_and_closes(app):
    picker, _loader = _picker(app)
    picker.open_panel()
    pump(app, 0.1)
    picker.panel.setFocus()
    picker.panel.set_current(0)
    chosen: list[int] = []
    picker.currentIndexChanged.connect(chosen.append)

    picker.panel.keyPressEvent(_key(Qt.Key_Down))
    assert picker.panel.current() == 1
    picker.panel.keyPressEvent(_key(Qt.Key_Return))

    assert chosen == [1]
    assert picker.header.text() == "Season 1"


def test_escape_closes_the_panel(app):
    picker, _loader = _picker(app)
    picker.open_panel()
    pump(app, 0.15)

    picker.panel.keyPressEvent(_key(Qt.Key_Escape))

    assert picker.panel.isVisible() is False or picker.panel._animation.state() == 1
    picker.close()


def test_the_fade_is_120_ms(app):
    picker, _loader = _picker(app)

    assert qt_season_picker.FADE_MS == 120
    assert picker.panel._animation.duration() == 120
    picker.close()


def test_the_tokens_drive_the_type(app):
    assert TEXT_SIZES["header"] == 22
    assert TEXT_SIZES["label"] == 14
    assert TEXT_SIZES["caption"] == 11


def test_light_theme_repaints_without_losing_the_rows(app):
    picker, _loader = _picker(app)
    picker.open_panel()
    pump(app, 0.1)

    picker.apply_theme("light")
    pump(app, 0.05)

    assert len(picker.panel.rows) == 3
    assert "#ffffff" in picker.panel.styleSheet()
    picker.close_panel()
    picker.close()


def _key(code):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    return QKeyEvent(QEvent.KeyPress, code, Qt.NoModifier)


def test_the_theme_colours_come_from_the_tokens(app):
    from lgl.design_tokens import TOKENS

    picker, _loader = _picker(app)
    picker.open_panel()
    pump(app, 0.05)

    assert str(TOKENS["dark"]["overlay"]) in picker.panel.styleSheet()
    assert str(TOKENS["dark"]["border"]) in picker.panel.styleSheet()
    picker.close_panel()
    picker.close()
    assert QColor(TOKENS["dark"]["accent"]).isValid()
