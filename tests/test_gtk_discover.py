"""The GTK Discover window; skipped without GTK 3 or a display."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_discover  # noqa: E402

ITEMS = [
    {"key": "a", "title": "Alpha", "genres": ["RPG"], "rating": 9.0, "popularity": 10, "year": "1998"},
    {"key": "b", "title": "Beta", "genres": ["RPG"], "rating": 6.0, "popularity": 80, "year": "2000"},
    {"key": "c", "title": "Gamma", "genres": ["RPG", "Puzzle"], "rating": 8.0, "popularity": 5, "year": "2004"},
]


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class Parent:
    def __init__(self):
        self._games = [
            SimpleNamespace(identifier="b", title="Beta", year="2000", subjects=["RPG"]),
        ]
        self.selected = None

    def _select_game(self, game):
        self.selected = game


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def titles(window, tab):
    return [item["title"] for item in window.items_in(tab)]


def test_recommendations_share_a_genre_and_skip_what_is_owned(display):
    window = gtk_discover.DiscoverWindow(Parent(), Store(), ITEMS, liked=["a"], owned=["a"])
    try:
        assert set(titles(window, "For you")) == {"Beta", "Gamma"}
    finally:
        window.destroy()


def test_hidden_gems_need_a_good_rating_and_low_popularity(display):
    window = gtk_discover.DiscoverWindow(Parent(), Store(), ITEMS)
    try:
        assert set(titles(window, "Hidden gems")) == {"Alpha", "Gamma"}
    finally:
        window.destroy()


def test_collection_gaps_prefer_the_better_rated_fill(display):
    window = gtk_discover.DiscoverWindow(Parent(), Store(), ITEMS, owned=["a"])
    try:
        assert titles(window, "Complete the set")[0] == "Gamma"
    finally:
        window.destroy()


def test_more_like_this_follows_the_seed(display):
    window = gtk_discover.DiscoverWindow(Parent(), Store(), ITEMS)
    try:
        assert window.seed.get_active_id() == "a"
        assert set(titles(window, "More like this")) == {"Beta", "Gamma"}
        window.seed.set_active_id("c")
        assert "Beta" in titles(window, "More like this")
    finally:
        window.destroy()


def test_selecting_a_title_opens_it_through_the_window(display):
    parent = Parent()
    rows = gtk_discover.game_rows(parent._games)
    window = gtk_discover.DiscoverWindow(parent, Store(), rows, owned=["b"])
    try:
        listbox = window._lists["For you"]
        assert listbox.get_row_at_index(0) is not None
        window._on_row(listbox, listbox.get_row_at_index(0))
        assert parent.selected is parent._games[0]
    finally:
        window.destroy()


def test_game_rows_skip_entries_without_an_identifier():
    rows = gtk_discover.game_rows([
        SimpleNamespace(identifier="", title="No key", year="", subjects=[]),
        SimpleNamespace(identifier="x", title="Kept", year="2001", subjects=["Puzzle"]),
    ])
    assert [row["key"] for row in rows] == ["x"]
    assert rows[0]["genres"] == ["Puzzle"]
