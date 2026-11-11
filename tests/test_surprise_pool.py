"""Which pool "Surprise me" draws from: the open page decides, never the others."""

from __future__ import annotations

from types import SimpleNamespace

from lgl import surprise_pool


def game(identifier, title):
    return SimpleNamespace(identifier=identifier, title=title)


def card(kind, key, title):
    return SimpleNamespace(kind=kind, key=key, title=title)


GAMES = [game("g1", "Alpha"), game("g2", "Beta")]
MEDIA = [
    card("movie", "movie:1", "A Film"),
    card("tv", "tv:2", "A Show"),
    card("anime", "anime:3", "A Series"),
]


def test_library_and_browse_pools_hold_only_games():
    for page in ("library", "browse", "archive"):
        pool = surprise_pool.pool_for(page, games=GAMES, media=MEDIA)
        assert pool == [
            {"kind": "game", "key": "g1", "title": "Alpha"},
            {"kind": "game", "key": "g2", "title": "Beta"},
        ], page


def test_the_movies_section_picks_only_movies():
    pool = surprise_pool.pool_for("media", media=MEDIA, section="movies")
    assert [row["kind"] for row in pool] == ["movie"]


def test_the_anime_section_picks_only_anime():
    pool = surprise_pool.pool_for("media", media=MEDIA, section="anime")
    assert [row["key"] for row in pool] == ["anime:3"]


def test_the_tv_section_picks_only_tv():
    pool = surprise_pool.pool_for("media", media=MEDIA, section="tv")
    assert [row["key"] for row in pool] == ["tv:2"]


def test_a_section_without_one_kind_offers_every_media_card():
    pool = surprise_pool.pool_for("media", media=MEDIA, section="home")
    assert {row["kind"] for row in pool} == {"movie", "tv", "anime"}


def test_the_media_pool_never_carries_games():
    pool = surprise_pool.pool_for("media", games=GAMES, media=MEDIA, section="movies")
    assert all(row["kind"] != "game" for row in pool)


def test_a_page_without_context_still_mixes_the_two():
    pool = surprise_pool.pool_for("", games=GAMES, media=MEDIA)
    assert [row["kind"] for row in pool] == ["game", "game", "movie", "tv", "anime"]


def test_empty_pages_give_empty_pools_rather_than_errors():
    assert surprise_pool.pool_for("library") == []
    assert surprise_pool.pool_for("media", section="anime") == []
    assert surprise_pool.pool_for("", games=[], media=[]) == []


def test_rows_without_a_key_are_skipped():
    assert surprise_pool.game_rows([game("", "Nameless")]) == []
    assert surprise_pool.media_rows([card("movie", "", "Nameless")]) == []


def test_mapping_entries_work_as_well_as_objects():
    rows = surprise_pool.game_rows([{"identifier": "g9", "title": "Nine"}])
    assert rows == [{"kind": "game", "key": "g9", "title": "Nine"}]
