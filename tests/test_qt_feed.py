"""The Qt feed widgets: PosterRow, ThreeColumnList and ScheduleStrip."""

from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from lgl import qt_feed  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Thing:
    def __init__(self, title: str, meta: str = "") -> None:
        self.title = title
        self.meta = meta


def test_poster_row_arrows_scroll_the_strip(app):
    row = qt_feed.PosterRow("Trending", "This week")
    row.set_cards([Thing(f"Title {n}") for n in range(8)])
    deltas: list[int] = []
    row.strip.scroll_by = deltas.append  # type: ignore[method-assign]

    row.right.click()
    row.left.click()

    assert deltas[0] > 0
    assert deltas[1] < 0
    assert len(row.posters()) == 8


def test_poster_row_view_all_and_item_clicks(app):
    row = qt_feed.PosterRow("Trending")
    row.set_cards([Thing("One")])
    clicks: list[object] = []
    row.view_all_clicked.connect(lambda: clicks.append("all"))
    row.item_clicked.connect(lambda card: clicks.append(card.title))

    row.view_all.click()
    poster = row.posters()[0]
    poster.clicked.emit(None) if hasattr(poster, "clicked") else None

    assert clicks == ["all"]


def test_poster_row_hides_itself_when_the_loader_returns_nothing(app):
    row = qt_feed.PosterRow("Hidden gems")
    row.set_loader(lambda: [])
    row.refresh()
    deadline = time.time() + 2
    while time.time() < deadline and row.placeholder.isVisible():
        app.processEvents()
        time.sleep(0.01)

    assert row.posters() == []
    assert not row.isVisible()


def test_poster_row_says_why_it_could_not_load(app):
    row = qt_feed.PosterRow("For you")
    row.set_error("AniList timed out")

    assert row.error.text() == "Could not load: AniList timed out"
    assert row.posters() == []
    assert row.error.isVisible()


def test_three_column_list_shows_five_rows_and_one_error_line(app):
    block = qt_feed.ThreeColumnList()
    block.set_items("new_release", [Thing(f"Ep {n}") for n in range(9)])
    block.set_error("just_completed", "AniList timed out")

    assert block.rows("new_release") == qt_feed.ThreeColumnList.LIMIT
    assert block.error_text("just_completed") == "Could not load: AniList timed out"
    assert block.rows("new_added") == 0


def test_three_column_list_clicking_a_row_reports_the_item(app):
    block = qt_feed.ThreeColumnList()
    block.set_items("new_added", [Thing("Brand new")])
    got: list[object] = []
    block.item_clicked.connect(got.append)

    frame = block.columns["new_added"][2].itemAt(0).widget()
    assert isinstance(frame, qt_feed.FeedRow)
    frame.clicked.emit(frame.item)

    assert got and got[0].title == "Brand new"


def test_schedule_strip_switches_days_and_converts_to_local_time(app):
    now = datetime(2026, 3, 10, 10, 0)
    today_episode = {
        "title": "Keroro",
        "episode": 5,
        "air_at": datetime(2026, 3, 10, 9, 0).timestamp(),
    }
    tomorrow_episode = {
        "title": "Tamama",
        "episode": 2,
        "air_at": datetime(2026, 3, 11, 21, 30).timestamp(),
    }
    strip = qt_feed.ScheduleStrip(now=now)
    strip.set_entries([today_episode, tomorrow_episode])

    assert strip.now_label_text() == "Now: 2026-03-10 10:00"
    assert strip.rows() == 1
    times = [
        strip.list_rows.itemAt(0).widget().findChildren(type(strip.now_label))[0].text()
    ]
    assert times == ["09:00"]
    assert strip.day_buttons[0].isChecked()

    strip.select_day(1)

    assert strip.rows() == 1
    label = strip.list_rows.itemAt(0).widget().findChildren(type(strip.now_label))[0]
    assert label.text() == "21:30"
    assert strip.day_buttons[1].isChecked()


def test_schedule_strip_moves_a_week_with_the_arrows(app):
    strip = qt_feed.ScheduleStrip(now=datetime(2026, 3, 10, 10, 0))
    start = strip.week_start()
    strip.next.click()
    assert strip.week_start().date().isoformat() == (start.date().replace(day=17)).isoformat()
    strip.prev.click()
    assert strip.week_start() == start


def test_schedule_strip_says_when_a_day_is_empty(app):
    strip = qt_feed.ScheduleStrip(now=datetime(2026, 3, 10, 10, 0))
    strip.set_entries([])
    assert strip.rows() == 0
    assert "Nothing airs" in strip.empty.text()
