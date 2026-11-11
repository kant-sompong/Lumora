"""The GTK "Surprise me…" pick; skipped without GTK 3."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
except ValueError:  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import discovery, gtk_surprise


def game(identifier, title):
    return SimpleNamespace(identifier=identifier, title=title)


def card(kind, key, title):
    return SimpleNamespace(kind=kind, key=key, title=title)


class FakeParent:
    def __init__(self, entries=(), cards=None, store=True):
        self.library_store = (
            SimpleNamespace(entries=lambda: list(entries)) if store else None
        )
        if cards is not None:
            self.media_view = SimpleNamespace(loaded_cards=lambda: list(cards))
        self.toasts = []
        self.views = []
        self.selected = []
        parent = self
        self.library_view = SimpleNamespace(
            _select_saved_tile=lambda key: parent.selected.append(key) or False
        )

    def toast(self, text, error=False):
        self.toasts.append((text, error))

    def _apply_view(self, name):
        self.views.append(name)


def test_pool_is_built_from_entries():
    pool = gtk_surprise.build_pool([game("a", "Alpha"), game("b", "")])
    assert pool == [
        {"kind": "game", "key": "a", "title": "Alpha"},
        {"kind": "game", "key": "b", "title": "b"},
    ]


def test_pool_keeps_kind_and_key_of_media_cards():
    pool = gtk_surprise.build_pool(
        [game("a", "Alpha")], [card("movie", "movie:7", "A Film")]
    )
    assert pool[-1] == {"kind": "movie", "key": "movie:7", "title": "A Film"}
    assert [row["kind"] for row in pool] == ["game", "movie"]


def test_an_entry_without_a_key_is_skipped():
    assert gtk_surprise.build_pool([game("", "Nameless")]) == []


def test_an_empty_pool_has_no_pick():
    assert discovery.random_pick(gtk_surprise.build_pool([])) is None
    parent = FakeParent()
    assert gtk_surprise.surprise(parent) is None
    assert parent.toasts == [(gtk_surprise.EMPTY_MESSAGE, True)]
    assert parent.views == []
    assert parent.selected == []


def test_a_seeded_generator_gives_a_predictable_pick():
    pool = gtk_surprise.build_pool(
        [game("a", "Alpha"), game("b", "Beta"), game("c", "Gamma")]
    )
    expected = random.Random(7).choice(pool)
    assert discovery.random_pick(pool, rng=random.Random(7)) is expected


def test_a_game_pick_is_announced_and_selected():
    parent = FakeParent([game("a", "Alpha"), game("b", "Beta")])
    choice = gtk_surprise.surprise(parent)
    assert choice is not None
    assert choice["kind"] == "game"
    assert choice["key"] in {"a", "b"}
    assert parent.toasts == [(f"Random pick: {choice['title']}", False)]
    assert parent.views == ["library"]
    assert parent.selected == [choice["key"]]


def test_a_media_key_opens_the_media_view():
    parent = FakeParent()
    gtk_surprise.jump_to(parent, "movie", "movie:7")
    assert parent.views == ["media"]
    assert parent.selected == []


class PageParent(FakeParent):
    """A window that knows which page is open and what Media shows."""

    def __init__(self, page, *, section="", cards=None, entries=(), games=()):
        super().__init__(entries, cards, store=bool(entries))
        self.page = page
        self.section = section
        self._games = list(games)
        if cards is not None:
            self.media_view = SimpleNamespace(
                loaded_cards=lambda: list(cards),
                _stack=SimpleNamespace(get_visible_child_name=lambda: section),
            )

    def active_view(self):
        return self.page


def test_the_library_page_picks_only_games():
    parent = PageParent(
        "library",
        entries=[game("a", "Alpha")],
        cards=[card("movie", "movie:1", "A Film")],
    )
    pool = gtk_surprise.pool_for_page(parent)
    assert [row["kind"] for row in pool] == ["game"]


def test_the_browse_page_picks_from_browse_games():
    parent = PageParent(
        "archive",
        entries=[game("lib", "Library Game")],
        games=[game("browse", "Browse Game")],
    )
    pool = gtk_surprise.pool_for_page(parent)
    assert [row["key"] for row in pool] == ["browse"]


def test_the_media_page_picks_only_the_open_section():
    parent = PageParent(
        "media",
        section="anime",
        cards=[card("movie", "movie:1", "A Film"), card("anime", "anime:2", "A Series")],
    )
    pool = gtk_surprise.pool_for_page(parent)
    assert [row["key"] for row in pool] == ["anime:2"]


def test_an_empty_page_never_crashes_or_navigates():
    parent = PageParent("media", section="anime", cards=[])
    assert gtk_surprise.pool_for_page(parent) == []
    assert gtk_surprise.surprise(parent) is None
    assert parent.toasts == [(gtk_surprise.EMPTY_MESSAGE, True)]
