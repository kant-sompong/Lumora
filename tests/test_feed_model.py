"""The toolkit-independent feed logic: posters, columns and schedule math.

No GTK and no Qt is imported here, so these run on any machine and the two
frontends are judged against the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lgl import feed_model


@dataclass
class Card:
    title: str = ""
    meta: str = ""


# -- cards ------------------------------------------------------------------
def test_a_field_reads_both_a_mapping_and_an_attribute_object():
    assert feed_model.field({"title": "Heat"}, "title") == "Heat"
    assert feed_model.field(Card(title="Heat"), "title") == "Heat"
    assert feed_model.field(Card(), "title") == ""
    assert feed_model.field({"meta": None}, "meta") == ""


def test_a_poster_shows_untitled_and_a_dash_for_missing_text():
    assert feed_model.poster_text({"title": "Heat", "meta": "1995"}) == ("Heat", "1995")
    assert feed_model.poster_text({}) == ("Untitled", "—")
    # Only an empty title counts as missing, as the Qt poster has always done.
    assert feed_model.poster_text(Card(title="  ")) == ("  ", "—")


def test_an_error_line_is_prefixed_and_an_empty_reason_gives_nothing():
    assert feed_model.error_line("offline") == "Could not load: offline"
    assert feed_model.error_line("  ") == ""
    assert feed_model.error_line(None) == ""


# -- poster row -------------------------------------------------------------
def test_a_poster_row_starts_idle_and_invisible():
    state = feed_model.PosterRowState()
    assert state.status == feed_model.IDLE
    assert state.visible is False
    assert state.cards == []


def test_loading_shows_a_placeholder_and_no_cards():
    state = feed_model.PosterRowState()
    state.set_cards([{"title": "Heat"}])
    state.set_loading()
    assert state.status == feed_model.LOADING
    assert state.visible is True
    assert state.showing_cards is False
    assert state.placeholder == "Loading…"
    assert state.error == ""


def test_loaded_cards_are_counted_and_titled():
    state = feed_model.PosterRowState()
    state.set_cards([{"title": "Heat"}, {"title": "Alien"}])
    assert state.status == feed_model.LOADED
    assert state.visible is True
    assert state.count == 2
    assert state.titles() == ["Heat", "Alien"]
    assert state.showing_cards is True


def test_no_cards_hides_the_row_and_an_error_shows_one_line():
    state = feed_model.PosterRowState()
    state.set_cards([])
    assert state.visible is False

    state.set_cards([{"title": "Heat"}])
    state.set_error("network down")
    assert state.status == feed_model.ERROR
    assert state.visible is True
    assert state.cards == []
    assert state.error == "Could not load: network down"


def test_an_empty_error_hides_the_row_again():
    state = feed_model.PosterRowState()
    state.set_error("boom")
    state.set_error("")
    assert state.status == feed_model.HIDDEN
    assert state.error == ""
    assert state.visible is False


def test_fit_count_never_shows_more_cards_than_there_are():
    state = feed_model.PosterRowState(card_width=150, spacing=10)
    assert state.fit_count(150) == 1
    # 3 cards and 2 gaps: 3 * 150 + 2 * 10 = 470.
    assert state.fit_count(470) == 3
    assert state.fit_count(469) == 2
    assert state.fit_count(100) == 1


def test_fit_count_is_capped_by_the_loaded_cards():
    state = feed_model.PosterRowState(card_width=150, spacing=10)
    state.set_cards([{"title": str(index)} for index in range(2)])
    assert state.fit_count(2000) == 2


# -- three columns ----------------------------------------------------------
def test_columns_start_empty_and_invisible():
    state = feed_model.ThreeColumnState()
    for key in ("new_release", "new_added", "just_completed"):
        assert state.visible(key) is False
        assert state.row_count(key) == 0
    assert state.any_visible() is False


def test_set_columns_fills_all_three_and_caps_rows_at_five():
    state = feed_model.ThreeColumnState()
    state.set_columns(
        new_release=[{"title": str(index)} for index in range(8)],
        new_added=[{"title": "One"}],
        just_completed=[],
    )
    assert state.row_count("new_release") == 5
    assert state.row_count("new_added") == 1
    assert state.visible("just_completed") is False
    assert state.any_visible() is True
    assert state.visible("new_release") is True


def test_show_all_expands_one_column_then_show_fewer_collapses_it():
    state = feed_model.ThreeColumnState()
    state.set_items("new_release", [{"title": str(index)} for index in range(8)])
    assert state.can_expand("new_release") is True
    assert state.toggle_label("new_release") == feed_model.SHOW_ALL

    assert state.toggle("new_release") is True
    assert state.row_count("new_release") == 8
    assert state.toggle_label("new_release") == feed_model.SHOW_FEWER

    assert state.toggle("new_release") is False
    assert state.row_count("new_release") == 5


def test_five_or_fewer_items_offer_no_show_all():
    state = feed_model.ThreeColumnState()
    state.set_items("new_added", [{"title": "One"}])
    assert state.can_expand("new_added") is False
    assert state.toggle("new_added") is False
    assert state.row_count("new_added") == 1


def test_a_column_error_clears_its_rows_and_names_the_reason():
    state = feed_model.ThreeColumnState()
    state.set_items("new_added", [{"title": "One"}])
    state.set_error("new_added", "timeout")
    assert state.error_text("new_added") == "Could not load: timeout"
    assert state.row_count("new_added") == 0
    assert state.visible("new_added") is True


def test_a_cleared_error_hides_the_column():
    state = feed_model.ThreeColumnState()
    state.set_error("just_completed", "timeout")
    state.set_error("just_completed", "")
    assert state.error_text("just_completed") == ""
    assert state.visible("just_completed") is False


# -- schedule ---------------------------------------------------------------
NOW = datetime(2026, 11, 10, 15, 30)


def airing(title: str, when: datetime, episode: object = 1) -> dict:
    return {"title": title, "air_at": when.timestamp(), "episode": episode}


def test_the_week_starts_on_the_day_the_clock_shows():
    state = feed_model.ScheduleState(now=NOW)
    assert state.week_start == datetime(2026, 11, 10)
    assert state.now_text() == "Now: 2026-11-10 15:30"
    assert state.selected_day() == datetime(2026, 11, 10)
    assert state.day_dates()[0].isoformat() == "2026-11-10"
    assert len(state.day_dates()) == 7
    assert state.is_today(0) is True
    assert state.is_today(1) is False


def test_shifting_the_week_moves_seven_days_and_resets_the_day():
    state = feed_model.ScheduleState(now=NOW)
    state.select_day(3)
    state.shift_week(7)
    assert state.week_start == datetime(2026, 11, 17)
    assert state.selected == 0
    state.shift_week(-14)
    assert state.week_start == datetime(2026, 11, 3)


def test_selecting_a_day_is_clamped_to_the_week():
    state = feed_model.ScheduleState(now=NOW)
    state.select_day(2)
    assert state.selected_day() == datetime(2026, 11, 12)
    state.select_day(99)
    assert state.selected == 6
    state.select_day(-5)
    assert state.selected == 0


def test_entries_are_sorted_and_filtered_by_the_selected_day():
    state = feed_model.ScheduleState(now=NOW)
    state.set_entries([
        airing("Late", datetime(2026, 11, 10, 22, 0)),
        airing("Early", datetime(2026, 11, 10, 9, 0)),
        airing("Tomorrow", datetime(2026, 11, 11, 9, 0)),
    ])
    assert [row["title"] for row in state.entries_for_day()] == ["Early", "Late"]
    state.select_day(1)
    assert [row["title"] for row in state.entries_for_day()] == ["Tomorrow"]
    assert state.rows() == state.entries_for_day()


def test_an_entry_without_an_air_date_is_dropped():
    state = feed_model.ScheduleState(now=NOW)
    state.set_entries([{"title": "Someday"}, airing("Today", NOW)])
    assert [row["title"] for row in state.entries] == ["Today"]


def test_an_empty_day_says_so():
    state = feed_model.ScheduleState(now=NOW)
    assert state.empty_text() == "Nothing airs on Tuesday 10 November."
    assert state.entries_for_day() == []


def test_the_clock_time_is_local_24_hour():
    assert feed_model.minutes_text(datetime(2026, 11, 10, 22, 5)) == "22:05"
    assert feed_model.minutes_text(datetime(2026, 11, 10, 7, 0)) == "07:00"
