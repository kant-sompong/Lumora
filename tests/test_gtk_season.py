"""The GTK season comparison window, on a display when there is one.

Skips itself without GTK 3 or without a display, exactly like the other GTK
window tests; the data itself is covered offline in ``test_season_data``.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_season  # noqa: E402


def media(ident, english, score=80, episodes=12):
    return {
        "id": ident,
        "title": {"english": english, "romaji": english},
        "averageScore": score,
        "episodes": episodes,
        "format": "TV",
        "status": "FINISHED",
    }


def post(body):
    season = body["variables"]["season"]
    rows = (
        [media(1, "Frieren", 90, 28), media(2, "Solo Leveling", 82)]
        if season == "WINTER"
        else [media(2, "Solo Leveling", 82), media(3, "Kaiju No. 8", 78)]
    )
    return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": rows}}}


def window(tmp_path, sender=post):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    return gtk_season.SeasonCompareWindow(
        post=sender, cache_path=tmp_path / "season-compare.json", threaded=False
    )


def rows(listing):
    return [row.get_child().get_text() for row in listing.get_children()]


def test_compare_fills_the_three_lists_and_the_summary(tmp_path):
    win = window(tmp_path)
    try:
        win.season_a.set_active_id("WINTER")
        win.season_b.set_active_id("SPRING")
        win.year_a.set_active_id("2024")
        win.year_b.set_active_id("2024")

        win.compare()

        assert rows(win.lists["only_first"]) == ["Frieren — score 90 · TV"]
        assert rows(win.lists["only_second"]) == ["Kaiju No. 8 — score 78 · TV"]
        assert rows(win.lists["shared"]) == ["Solo Leveling — score 82 · TV"]
        assert win.summary.get_text() == (
            "2024 winter: 2 titles, 2024 spring: 2 titles, 1 in both"
        )
        assert win.error.get_visible() is False
        assert win.detail.get_text() == gtk_season.HINT
    finally:
        win.destroy()


def test_selecting_a_title_shows_its_score_and_episodes(tmp_path):
    win = window(tmp_path)
    try:
        win.season_a.set_active_id("WINTER")
        win.season_b.set_active_id("SPRING")
        win.year_a.set_active_id("2024")
        win.year_b.set_active_id("2024")
        win.compare()

        win.lists["shared"].select_row(win.lists["shared"].get_row_at_index(0))

        assert "Score 82/100" in win.detail.get_text()
        assert "12 episodes" in win.detail.get_text()
    finally:
        win.destroy()


def test_a_network_failure_is_a_line_not_an_empty_window(tmp_path):
    def offline(_body):
        raise TimeoutError("timed out")

    win = window(tmp_path, sender=offline)
    try:
        win.season_a.set_active_id("WINTER")
        win.season_b.set_active_id("SPRING")
        win.year_a.set_active_id("2024")
        win.year_b.set_active_id("2024")

        win.compare()

        assert win.error.get_visible() is True
        assert win.error.get_text().startswith("Could not load: ")
        assert "timed out" in win.error.get_text()
        assert win.summary.get_text() == "Nothing to compare"
        assert win.season_a.get_active_id() == "WINTER"
        assert all(not listing.get_children() for listing in win.lists.values())
    finally:
        win.destroy()
