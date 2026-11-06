"""The GTK statistics window; skipped without GTK 3 or a display."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_stats, stats


def stamp(day: str) -> float:
    return dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp()


def game(identifier, title, added=0.0, played=0.0, genres=(), **extra):
    return SimpleNamespace(
        identifier=identifier,
        title=title,
        added_at=added,
        last_played=played,
        genres=tuple(genres),
        cover_path=extra.get("cover_path", ""),
        description=extra.get("description", ""),
        year=extra.get("year", ""),
    )


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def window(entries=(), viewed=()):
    return gtk_stats.StatisticsWindow(None, entries, viewed)


def test_events_skip_missing_timestamps():
    entries = [
        game("a", "Alpha", added=100.0, played=200.0),
        game("b", "Beta", added=0.0, played=None),
    ]
    events = gtk_stats.build_events(
        entries, [{"title": "Gamma", "kind": "movie", "viewed_at": 300.0}]
    )
    assert [event["ts"] for event in events] == [100.0, 200.0, 300.0]
    assert [event["title"] for event in events] == ["Alpha", "Alpha", "Gamma"]


def test_totals_count_events_days_and_streak(display):
    entries = [
        game("a", "Alpha", added=stamp("2026-01-05")),
        game("b", "Beta", added=stamp("2026-01-06"), played=stamp("2026-01-06")),
        game("c", "Gamma", added=stamp("2026-01-08")),
    ]
    win = window(entries)
    try:
        assert win.totals == {
            "events": 4,
            "days": 3,
            "streak": 2,
        }
        assert win.totals == stats.totals(win.events)
        assert "4 events" in win.summary.get_text()
        assert "3 active days" in win.summary.get_text()
        assert "streak 2 days" in win.summary.get_text()
        assert win.busiest_label.get_text() == (
            "Busiest day: 2026-01-06 (2 events)"
        )
    finally:
        win.destroy()


def test_heatmap_grid_is_seven_by_twelve_with_counts():
    events = [
        {"ts": stamp("2026-01-10")},
        {"ts": stamp("2026-01-10")},
        {"ts": stamp("2026-01-04")},
        {"ts": "not a date"},
    ]
    grid = gtk_stats.heatmap_grid(events, 12, today="2026-01-10")
    assert len(grid) == 7
    assert all(len(row) == 12 for row in grid)
    # 2026-01-10 is a Saturday (row 5) and lands in the last column.
    assert grid[5][11] == 2
    # 2026-01-04 is a Sunday (row 6) in the same week.
    assert grid[6][11] == 1
    assert sum(sum(row) for row in grid) == 3


def test_an_empty_library_says_so_and_keeps_zero_totals(display):
    win = window()
    try:
        assert win.totals == {"events": 0, "days": 0, "streak": 0}
        assert win.busiest is None
        assert win.busiest_label.get_text() == "No activity yet."
        assert "0 titles" in win.summary.get_text()
        assert "0 events" in win.summary.get_text()
        assert "No events recorded yet" in win.empty.get_text()
        assert all(not any(row) for row in win.grid)
    finally:
        win.destroy()


def test_genres_and_coverage_are_summarised(display):
    entries = [
        game(
            "a", "Alpha", added=stamp("2026-01-05"),
            genres=["Action", "RPG"], cover_path="/a.jpg", year="2024",
        ),
        game(
            "b", "Beta", added=stamp("2026-01-05"),
            genres=["Action"], description="text",
        ),
    ]
    win = window(entries)
    try:
        assert win.genres == [("Action", 2), ("RPG", 1)]
        assert win.coverage["titles"] == 2
        assert win.coverage["poster"] == 1
        assert "Action 2" in win.genres_label.get_text()
        assert "posters 1/2" in win.coverage_label.get_text()
        assert win.empty.get_text() == ""
    finally:
        win.destroy()


def test_the_window_does_not_shadow_container_add_and_remove(display):
    for name in ("add", "remove"):
        assert name not in vars(gtk_stats.StatisticsWindow), (
            f"StatisticsWindow overrides Gtk.Container.{name}"
        )
    assert gtk_stats.StatisticsWindow.add is Gtk.Container.add
    assert gtk_stats.StatisticsWindow.remove is Gtk.Container.remove
