"""The GTK 3 feed widgets: poster rows, three columns and the schedule strip.

The module skips itself when GTK 3 or a display is missing; CI's Linux job
installs Xvfb and runs them for real.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_feed  # noqa: E402

NOW = datetime(2026, 11, 10, 15, 30)


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def pump(seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def cards(count: int) -> list[dict]:
    return [
        {"title": f"Title {index}", "meta": f"Meta {index}"}
        for index in range(count)
    ]


def in_window(widget: Gtk.Widget, width: int = 460, height: int = 460):
    window = Gtk.OffscreenWindow()
    window.set_default_size(width, height)
    window.add(widget)
    window.show_all()
    pump()
    return window


# -- poster row -------------------------------------------------------------
def test_a_poster_row_is_hidden_until_it_has_something(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    assert row.get_visible() is False
    assert row.cards() == []
    assert row.posters() == []


def test_loading_shows_one_quiet_line_and_no_cards(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_loading()
    assert row.get_visible() is True
    assert row.placeholder.get_visible() is True
    assert row.placeholder.get_text() == "Loading…"
    assert row.body.get_visible() is False
    assert row.posters() == []


def test_loaded_cards_become_posters_with_their_text(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_cards(cards(3))
    assert row.get_visible() is True
    assert row.body.get_visible() is True
    assert len(row.posters()) == 3
    assert row.cards() == cards(3)
    assert row.count.get_text() == "3"
    first = row.posters()[0]
    assert first.title.get_text() == "Title 0"
    assert first.meta.get_text() == "Meta 0"


def test_empty_cards_hide_the_row_again(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_cards(cards(2))
    row.set_cards([])
    assert row.get_visible() is False
    assert row.posters() == []


def test_an_error_is_one_dim_line_and_hides_the_posters(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_cards(cards(2))
    row.set_error("network down")
    assert row.get_visible() is True
    assert row.error.get_visible() is True
    assert row.error.get_text() == "Could not load: network down"
    assert row.body.get_visible() is False

    row.set_error("")
    assert row.get_visible() is False


def test_refresh_runs_the_loader_and_shows_its_cards(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_loader(lambda: cards(4))
    row.refresh()
    assert row.loader() is not None
    assert len(row.posters()) == 4
    assert row.count.get_text() == "4"


def test_a_failing_loader_leaves_one_error_line(display):
    def broken():
        raise RuntimeError("no connection")

    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_loader(broken)
    row.refresh()
    assert row.error.get_visible() is True
    assert "no connection" in row.error.get_text()
    assert row.posters() == []


def test_a_threaded_loader_delivers_on_the_main_loop(display):
    row = gtk_feed.PosterRow("Trending", threaded=True)
    row.set_loader(lambda: cards(2))
    row.refresh()
    pump(0.3)
    assert len(row.posters()) == 2


def test_clicking_a_poster_calls_the_rows_handler(display):
    seen: list[object] = []
    row = gtk_feed.PosterRow("Trending", threaded=False, on_open=seen.append)
    row.set_cards(cards(2))
    row.posters()[1].clicked()
    assert seen == [cards(2)[1]]


def test_the_cover_loader_supplies_the_image(display):
    seen: list[object] = []

    def cover(card):
        seen.append(card)
        return None

    row = gtk_feed.PosterRow("Trending", threaded=False, cover_loader=cover)
    row.set_cards(cards(2))
    assert seen == cards(2)


def test_the_arrows_move_the_visible_window(display):
    row = gtk_feed.PosterRow("Trending", threaded=False)
    row.set_cards(cards(12))
    window = in_window(row)
    adjustment = row.scroller.get_hadjustment()
    adjustment.set_value(0)
    pump()

    assert adjustment.get_upper() > adjustment.get_page_size()

    row.right.clicked()
    pump()
    after_right = adjustment.get_value()
    assert after_right > 0

    row.left.clicked()
    pump()
    assert adjustment.get_value() < after_right
    window.destroy()


# -- three columns ----------------------------------------------------------
def test_three_columns_start_empty(display):
    columns = gtk_feed.ThreeColumnList()
    assert columns.get_visible() is False
    for key in ("new_release", "new_added", "just_completed"):
        assert columns.rows(key) == 0
        assert columns.error_text(key) == ""


def test_a_column_shows_five_rows_until_it_is_expanded(display):
    columns = gtk_feed.ThreeColumnList()
    columns.set_items("new_release", cards(8))
    assert columns.get_visible() is True
    assert columns.rows("new_release") == 5
    assert columns.columns["new_release"]["toggle"].get_visible() is True

    columns.toggle("new_release")
    assert columns.rows("new_release") == 8
    columns.toggle("new_release")
    assert columns.rows("new_release") == 5


def test_a_short_column_has_no_show_all(display):
    columns = gtk_feed.ThreeColumnList()
    columns.set_columns(new_added=cards(2))
    assert columns.rows("new_added") == 2
    assert columns.columns["new_added"]["toggle"].get_visible() is False


def test_a_column_error_names_the_reason_and_hides_its_rows(display):
    columns = gtk_feed.ThreeColumnList()
    columns.set_items("just_completed", cards(3))
    columns.set_error("just_completed", "timeout")
    assert columns.error_text("just_completed") == "Could not load: timeout"
    assert columns.rows("just_completed") == 0
    assert columns.columns["just_completed"]["error"].get_visible() is True
    assert columns.columns["just_completed"]["rows"].get_visible() is False


# -- schedule ---------------------------------------------------------------
def airing(title: str, when: datetime, episode: object = 1) -> dict:
    return {"title": title, "air_at": when.timestamp(), "episode": episode}


def test_the_strip_shows_seven_days_and_the_clock(display):
    strip = gtk_feed.ScheduleStrip(now=NOW)
    assert len(strip.day_buttons) == 7
    assert strip.now_text() == "Now: 2026-11-10 15:30"
    assert strip.week_start() == datetime(2026, 11, 10)
    assert strip.selected_day() == datetime(2026, 11, 10)
    assert strip.day_buttons[0].get_active() is True


def test_shifting_the_week_moves_the_chips_and_resets_the_day(display):
    strip = gtk_feed.ScheduleStrip(now=NOW)
    strip.select_day(3)
    assert strip.selected_day() == datetime(2026, 11, 13)

    strip.shift_week(7)
    assert strip.week_start() == datetime(2026, 11, 17)
    assert strip.selected_day() == datetime(2026, 11, 17)
    assert strip.day_buttons[0].get_active() is True

    strip.shift_week(-14)
    assert strip.week_start() == datetime(2026, 11, 3)


def test_selecting_a_day_moves_the_chips_and_the_rows(display):
    strip = gtk_feed.ScheduleStrip(now=NOW)
    strip.set_entries([
        airing("Today", datetime(2026, 11, 10, 21, 0), 4),
        airing("Tomorrow", datetime(2026, 11, 11, 20, 30), 2),
    ])
    assert strip.rows() == 1
    assert strip.day_buttons[0].get_active() is True

    strip.select_day(1)
    assert strip.selected_day() == datetime(2026, 11, 11)
    assert strip.day_buttons[1].get_active() is True
    assert strip.day_buttons[0].get_active() is False
    assert [entry["title"] for entry in strip.entries_for_day()] == ["Tomorrow"]
    assert strip.rows() == 1

    strip.select_day(3)
    assert strip.rows() == 0
    assert strip.empty.get_visible() is True


def test_a_day_chip_click_selects_that_day(display):
    strip = gtk_feed.ScheduleStrip(now=NOW)
    strip.day_buttons[2].set_active(True)
    pump()
    assert strip.selected_day() == datetime(2026, 11, 12)
    assert strip.day_buttons[2].get_active() is True
