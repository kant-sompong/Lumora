"""The "Not aired yet" mark on the episode list.

The mark is a property of the row and a colour from the token set; the row
itself is never disabled, so the click path and the player behave as before.
Every date case is exercised with an explicit ``today``, so the suite cannot
start failing on New Year's Day.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_media, qt_theme  # noqa: E402
from lgl.design_tokens import TOKENS  # noqa: E402
from lgl.qt_media import NOT_AIRED_LABEL, EpisodeRow, air_date_status  # noqa: E402

TODAY = date(2026, 11, 15)
PAST = (TODAY - timedelta(days=30)).isoformat()
FUTURE = (TODAY + timedelta(days=7)).isoformat()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def _row(app, air_date: str, *, today: date | None = None) -> EpisodeRow:
    row = EpisodeRow(1, "The Long Night", air_date, runtime=66)
    row.resize(560, 80)
    row.show()
    pump(app)
    if today is not None:
        row.refresh_air_status(today)
    return row


# -- the date rule ----------------------------------------------------------

def test_a_past_air_date_is_aired(app):
    assert air_date_status(PAST, TODAY) == "aired"
    row = _row(app, PAST, today=TODAY)
    assert row.property("upcoming") is False
    assert row.status.isVisible() is False


def test_today_counts_as_aired(app):
    assert air_date_status(TODAY.isoformat(), TODAY) == "aired"
    row = _row(app, TODAY.isoformat(), today=TODAY)
    assert row.property("upcoming") is False
    assert row.status.isVisible() is False


def test_a_future_air_date_is_upcoming(app):
    assert air_date_status(FUTURE, TODAY) == "upcoming"
    row = _row(app, FUTURE, today=TODAY)
    assert row.property("upcoming") is True
    assert row.status.isVisible() is True
    assert row.status.text() == NOT_AIRED_LABEL == "Not aired yet"


@pytest.mark.parametrize("air_date", ["", "   ", "not a date", "2026-13-45", "2026", None])
def test_missing_or_invalid_dates_look_normal(app, air_date):
    assert air_date_status(air_date, TODAY) == "unknown"
    row = _row(app, air_date or "", today=TODAY)
    assert row.property("upcoming") is False
    assert row.status.isVisible() is False


def test_status_is_recomputed_when_the_day_changes(app):
    row = _row(app, (TODAY + timedelta(days=10)).isoformat(), today=TODAY)
    assert row.property("upcoming") is True
    assert row.status.isVisible() is True

    # A week on it is still in the future, so it stays marked.
    assert row.refresh_air_status(TODAY + timedelta(days=7)) is True
    assert row.status.isVisible() is True

    # Once the date has passed the row goes back to normal.
    assert row.refresh_air_status(TODAY + timedelta(days=30)) is False
    assert row.property("upcoming") is False
    assert row.status.isVisible() is False

    # ...and the other way round, when a schedule slips into the future.
    assert row.refresh_air_status(TODAY) is True
    assert row.property("upcoming") is True
    assert row.status.isVisible() is True


def test_the_page_rechecks_the_rows_when_the_day_rolls_over(app):
    """The media page owns a minute timer; a new day repaints its rows."""
    page = qt_media.MediaPage(None, cache_root=None)
    try:
        today_row = EpisodeRow(1, "The Long Night", TODAY.isoformat())
        later_row = EpisodeRow(2, "The Next One", (TODAY + timedelta(days=5)).isoformat())
        page.episode_column.addWidget(today_row)
        page.episode_column.addWidget(later_row)
        page._air_day = TODAY
        page._refresh_air_dates(TODAY)
        assert today_row.property("upcoming") is False
        assert later_row.property("upcoming") is True

        # Same day: nothing changes.
        page._refresh_air_dates(TODAY)
        assert later_row.property("upcoming") is True

        # Two days on, the second episode is still to come, the first is aired.
        page._refresh_air_dates(TODAY + timedelta(days=2))
        assert page._air_day == TODAY + timedelta(days=2)
        assert today_row.property("upcoming") is False
        assert later_row.property("upcoming") is True

        # Its date passes: the mark goes away without reloading the page.
        page._refresh_air_dates(TODAY + timedelta(days=6))
        assert later_row.property("upcoming") is False
        assert later_row.status.isVisible() is False
    finally:
        page.deleteLater()


# -- the ink ----------------------------------------------------------------

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_upcoming_mark_uses_the_danger_token(app, theme):
    qss = qt_theme.build_stylesheet(theme)
    danger = TOKENS[theme]["danger_text"]
    assert f'QFrame#episodeRow[upcoming="true"] QLabel#episodeTitle {{ color: {danger}; }}' in qss
    assert f"QLabel#episodeStatus {{ color: {danger};" in qss
    # No literal colour in either rule: both read the token.
    for line in qss.splitlines():
        if "episodeStatus" in line or "upcoming" in line:
            if "{" in line and "}" in line and "color" in line:
                assert danger in line


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_rendered_title_is_red_in_both_themes(app, theme):
    from PySide6.QtGui import QColor

    app.setStyleSheet(qt_theme.build_stylesheet(theme))
    try:
        row = _row(app, FUTURE, today=TODAY)
        pump(app, 0.1)
        image = row.title_label.grab().toImage()
        colors = [
            QColor(image.pixel(x, y)).name()
            for x in range(0, image.width(), 2)
            for y in range(0, image.height(), 2)
            if QColor(image.pixel(x, y)).alpha() == 255
        ]
        danger = QColor(TOKENS[theme]["danger_text"])
        closest = min(
            colors,
            key=lambda value: sum(
                abs(first - second)
                for first, second in zip(
                    QColor(value).getRgb()[:3], danger.getRgb()[:3]
                )
            ),
            default="",
        )
        assert colors, "the title label painted nothing"
        assert closest.lower() == danger.name().lower(), (
            f"{theme}: the upcoming title rendered {closest}, not {danger.name()}"
        )
    finally:
        app.setStyleSheet("")


# -- the interaction rule ---------------------------------------------------

def test_an_upcoming_episode_is_still_clickable(app):
    row = _row(app, FUTURE, today=TODAY)
    clicks: list[int] = []
    row.clicked.connect(lambda: clicks.append(1))

    assert row.isEnabled() is True
    assert row.cursor().shape() == Qt.PointingHandCursor
    QTest.mouseClick(row, Qt.LeftButton, pos=row.rect().center())

    assert clicks == [1]


def test_the_click_handler_is_the_same_for_aired_and_upcoming_rows(app):
    aired = _row(app, PAST, today=TODAY)
    upcoming = _row(app, FUTURE, today=TODAY)
    assert type(aired).mouseReleaseEvent is type(upcoming).mouseReleaseEvent
    for row in (aired, upcoming):
        assert row.isEnabled() is True
        assert row.cursor().shape() == Qt.PointingHandCursor
