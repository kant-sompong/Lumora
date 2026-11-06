"""The GTK Favourites and Activity windows; skipped without GTK 3 or a display."""

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

from lgl import gtk_activity


def game(identifier, title, favorite=False, added=0.0, seen=0.0, played=0.0):
    return SimpleNamespace(
        identifier=identifier,
        title=title,
        favorite=favorite,
        added_at=added,
        last_seen=seen,
        last_played=played,
    )


def stamp(day: str) -> float:
    return dt.datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp()


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def test_favourites_show_games_beside_media(display):
    rows = [
        {"key": "g1", "title": "Game one", "kind": "game"},
        {"key": "m1", "title": "A film", "kind": "movie"},
        {"key": "g2", "title": "Game two", "kind": "game"},
    ]
    window = gtk_activity.FavouritesWindow(None, rows)
    try:
        assert [row["title"] for row in window.games] == ["Game one", "Game two"]
        assert [row["title"] for row in window.media] == ["A film"]
        assert window.games_list.get_row_at_index(1) is not None
        assert window.media_note.get_text() == ""
        assert window.games_note.get_text() == ""
    finally:
        window.destroy()


def test_favourites_say_why_a_column_is_empty(display):
    window = gtk_activity.FavouritesWindow(None, [])
    try:
        assert "No favourites yet" in window.games_note.get_text()
        assert "not stored anywhere yet" in window.media_note.get_text()
        assert window.games_list.get_row_at_index(0) is None
    finally:
        window.destroy()


def test_only_favourite_games_become_rows(display):
    entries = [
        game("g1", "Hearted", favorite=True),
        game("g2", "Plain", favorite=False),
    ]
    rows = gtk_activity.favourite_rows(entries)
    assert [row["title"] for row in rows] == ["Hearted"]
    assert rows[0]["kind"] == "game"


def test_events_name_the_action_and_skip_junk(display):
    entries = [
        game("g1", "Added only", added=stamp("2026-01-01")),
        game("g2", "Played", added=0.0, played=stamp("2026-01-03")),
        game("g3", "Never touched"),
    ]
    viewed = [
        {"kind": "movie", "title": "A film", "viewed_at": stamp("2026-01-02")},
        {"kind": "movie", "title": "Broken", "viewed_at": None},
    ]
    events = gtk_activity.build_events(entries, viewed)
    labels = [event["label"] for event in events]
    assert "Added · Added only" in labels
    assert "Launched · Played" in labels
    assert "Watched · A film" in labels
    assert all("Never touched" not in label for label in labels)
    assert all("Broken" not in label for label in labels)


def test_activity_is_newest_first(display):
    events = [
        {"ts": stamp("2026-01-01"), "kind": "game", "label": "Added · Old"},
        {"ts": stamp("2026-01-05"), "kind": "game", "label": "Added · New"},
        {"ts": stamp("2026-01-03"), "kind": "game", "label": "Added · Middle"},
    ]
    window = gtk_activity.ActivityWindow(None, events)
    try:
        assert window.rows == [
            ("2026-01-05", "Added · New"),
            ("2026-01-03", "Added · Middle"),
            ("2026-01-01", "Added · Old"),
        ]
        assert window.empty.get_text() == ""
    finally:
        window.destroy()


def test_activity_says_why_it_is_empty(display):
    window = gtk_activity.ActivityWindow(
        None, [{"ts": "not a date", "label": "Junk"}, {"label": "No stamp"}]
    )
    try:
        assert window.rows == []
        assert "Nothing recorded yet" in window.empty.get_text()
    finally:
        window.destroy()


def test_favourites_window_reads_the_store(display):
    store = SimpleNamespace(
        entries=lambda: [
            game("g1", "Hearted", favorite=True),
            game("g2", "Plain"),
        ]
    )
    parent = SimpleNamespace(library_store=store)
    window = gtk_activity.show_favourites(parent)
    try:
        assert [row["title"] for row in window.games] == ["Hearted"]
    finally:
        window.destroy()
