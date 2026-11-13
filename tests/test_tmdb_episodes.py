"""Next-episode ordering: same season first, then the next season."""

from __future__ import annotations

from lgl.tmdb import (
    Episode,
    Season,
    following_episode,
    seasons_with_episodes,
)


def season(number: int, *episodes: tuple[int, str]) -> Season:
    return Season(
        number=number,
        episodes=tuple(
            Episode(season=number, number=count, title=title)
            for count, title in episodes
        ),
    )


SHOW = (
    season(1, (1, "Pilot"), (2, "Second")),
    season(2, (1, "Return"), (2, "Middle"), (3, "Finale")),
)


def test_the_next_episode_is_the_following_one_in_the_same_season():
    found = following_episode(SHOW, 1, 1)

    assert found is not None
    assert found[0] == 1
    assert found[1].number == 2


def test_the_last_episode_of_a_season_moves_to_the_next_season():
    found = following_episode(SHOW, 1, 2)

    assert found is not None
    assert found[0] == 2
    assert found[1].number == 1
    assert found[1].title == "Return"


def test_the_series_finale_has_nothing_after_it():
    assert following_episode(SHOW, 2, 3) is None


def test_an_unknown_episode_offers_nothing():
    assert following_episode(SHOW, 9, 1) is None
    assert following_episode((), 1, 1) is None


def test_a_season_without_episodes_is_skipped():
    show = (season(1, (1, "Pilot")), season(2), season(3, (1, "Third")))

    found = following_episode(show, 1, 1)

    assert found is not None
    assert found[0] == 3
    assert found[1].number == 1


def test_a_loaded_season_replaces_its_summary():
    """That is the real shape: details has counts, the tab has the episodes."""
    summary = Season(number=1, name="Season 1", episode_count=2)

    merged = seasons_with_episodes((summary,), {1: season(1, (1, "Pilot"), (2, "Next"))})

    assert len(merged) == 1
    assert [episode.number for episode in merged[0].episodes] == [1, 2]


def test_the_next_episode_is_found_once_a_season_is_loaded():
    show = (
        Season(number=4, episode_count=2),
        Season(number=5, episode_count=1),
    )
    loaded = {4: season(4, (1, "One"), (2, "Finale of four")), 5: season(5, (1, "Later"))}

    found = following_episode(seasons_with_episodes(show, loaded), 4, 2)

    assert found is not None
    assert found[0] == 5
    assert found[1].title == "Later"


def test_a_summary_alone_offers_nothing():
    show = (Season(number=4, episode_count=2),)

    assert following_episode(seasons_with_episodes(show, {}), 4, 1) is None


def test_an_empty_loaded_season_keeps_the_summary():
    show = (Season(number=1, episode_count=3),)

    merged = seasons_with_episodes(show, {1: Season(number=1)})

    assert merged[0].episode_count == 3


# --------------------------------------------------------------------------
# season artwork and the IMDb id (shared contract, section 5)
# --------------------------------------------------------------------------


def _client(fetch):
    from lgl.tmdb import TmdbClient

    return TmdbClient("a" * 32, fetch=fetch)


TV_PAYLOAD = {
    "name": "The Show",
    "first_air_date": "2019-03-01",
    "poster_path": "/show.jpg",
    "seasons": [
        {"season_number": 0, "name": "Specials", "episode_count": 2, "poster_path": "/s0.jpg"},
        {"season_number": 1, "name": "Season 1", "episode_count": 8, "poster_path": "/s1.jpg"},
        {"season_number": 2, "name": "Season 2", "episode_count": 6},
    ],
    "external_ids": {"imdb_id": "tt1234567"},
}


def test_details_asks_for_external_ids_and_keeps_the_imdb_id():
    seen = {}

    def fetch(path, params):
        seen["path"], seen["params"] = path, dict(params)
        return TV_PAYLOAD

    details = _client(fetch).details("tv", 42)

    assert seen["path"] == "/tv/42"
    assert seen["params"].get("append_to_response") == "external_ids"
    assert details.imdb_id == "tt1234567"


def test_a_malformed_imdb_id_is_dropped():
    def fetch(path, params):
        return {**TV_PAYLOAD, "external_ids": {"imdb_id": "not-an-id"}}

    assert _client(fetch).details("tv", 42).imdb_id == ""


def test_details_keeps_each_season_poster_path():
    def fetch(path, params):
        return TV_PAYLOAD

    seasons = {season.number: season for season in _client(fetch).details("tv", 42).seasons}

    assert seasons[1].poster == "/s1.jpg"
    assert seasons[2].poster == ""


def test_a_fetched_season_keeps_its_poster_path():
    def fetch(path, params):
        return {"name": "Season 1", "poster_path": "/s1.jpg", "episodes": []}

    season = _client(fetch).season(42, 1)

    assert season.poster == "/s1.jpg"


def test_season_poster_prefers_the_season_then_the_show():
    from lgl.tmdb import Season, poster_url, season_poster_url

    own = Season(number=1, poster="/s1.jpg")
    assert season_poster_url(own, "/show.jpg") == poster_url("/s1.jpg", size="w185")

    bare = Season(number=2)
    assert season_poster_url(bare, "/show.jpg") == poster_url("/show.jpg", size="w185")
    assert season_poster_url(bare, "https://image.tmdb.org/t/p/w500/show.jpg") == (
        "https://image.tmdb.org/t/p/w500/show.jpg"
    )


def test_season_poster_is_empty_with_neither_poster():
    from lgl.tmdb import Season, season_poster_url

    assert season_poster_url(Season(number=3)) == ""
    assert season_poster_url(Season(number=3, poster="not a path")) == ""
