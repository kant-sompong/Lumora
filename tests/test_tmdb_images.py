"""TMDB wide art: logos, textless backdrops and the fields episode rows need.

Everything here stubs ``fetch``, so no test touches the network and none needs a
real key.
"""

from __future__ import annotations

import pytest

from lgl import tmdb

KEY = "0123456789abcdef0123456789abcdef"


def client(payload, calls: list | None = None) -> tmdb.TmdbClient:
    """A client whose one answer is ``payload`` (or the error it raises)."""

    def fetch(path, params):
        if calls is not None:
            calls.append((path, dict(params)))
        if isinstance(payload, Exception):
            raise payload
        return payload

    return tmdb.TmdbClient(KEY, fetch=fetch)


IMAGES = {
    "logos": [
        {"file_path": "/neutral.png", "iso_639_1": None, "vote_average": 9.0},
        {"file_path": "/english.png", "iso_639_1": "en", "vote_average": 5.0},
    ],
    "backdrops": [
        {"file_path": "/textless.jpg", "iso_639_1": None,
         "vote_average": 4.0, "aspect_ratio": 1.78},
        {"file_path": "/english.jpg", "iso_639_1": "en",
         "vote_average": 8.0, "aspect_ratio": 1.78},
    ],
}


# -- images() ---------------------------------------------------------------


def test_the_english_logo_wins_even_with_a_higher_rated_neutral_one():
    found = client(IMAGES).images("tv", 1399)

    assert found.logo_url.endswith("/w500/english.png")


def test_a_jpg_logo_is_not_a_logo():
    found = client({"logos": [
        {"file_path": "/photo.jpg", "iso_639_1": "en", "vote_average": 9.0},
    ]}).images("tv", 1399)

    assert found.logo_url == ""


def test_the_neutral_logo_is_used_when_there_is_no_english_one():
    found = client({"logos": [
        {"file_path": "/neutral.svg", "iso_639_1": None, "vote_average": 2.0},
    ]}).images("movie", 603)

    assert found.logo_url.endswith("/w500/neutral.svg")


def test_the_textless_backdrop_wins_over_a_better_rated_english_one():
    found = client(IMAGES).images("tv", 1399)

    assert found.backdrop_url.endswith("/w1280/textless.jpg")


def test_a_tall_backdrop_is_not_wide_enough():
    found = client({"backdrops": [
        {"file_path": "/tall.jpg", "iso_639_1": None,
         "vote_average": 9.0, "aspect_ratio": 1.2},
    ]}).images("tv", 1399)

    assert found.backdrop_url == ""


def test_an_error_is_an_empty_result_not_an_exception():
    found = client(tmdb.TmdbError("TMDB answered HTTP 500.")).images("tv", 1399)

    assert found == tmdb.MediaImages()
    assert found.logo_url == "" and found.backdrop_url == ""


def test_a_missing_key_is_an_empty_result_and_the_key_never_leaks():
    found = tmdb.TmdbClient("").images("tv", 1399)

    assert found == tmdb.MediaImages()


def test_a_network_error_message_carries_no_key():
    error = tmdb.TmdbError(f"Could not reach TMDB: api_key={KEY}")
    found = client(error).images("tv", 1399)

    assert found == tmdb.MediaImages()
    assert KEY not in str(error)


def test_images_are_cached_for_the_session():
    calls: list = []
    stub = client(IMAGES, calls)

    first = stub.images("tv", 1399)
    second = stub.images("tv", 1399)

    assert first is second
    assert len(calls) == 1
    assert calls[0][0] == "/tv/1399/images"
    assert calls[0][1]["include_image_language"] == "en,null"


# -- season() ---------------------------------------------------------------

SEASON = {
    "name": "Season 1",
    "episodes": [
        {
            "season_number": 1,
            "episode_number": 1,
            "name": "Pilot",
            "air_date": "2022-09-01",
            "overview": "The one that starts it.",
            "still_path": "/still.jpg",
            "vote_average": 6.64,
            "runtime": 66,
        },
        {
            "season_number": 1,
            "episode_number": 2,
            "name": "Unrated",
            "air_date": "2022-09-08",
            "vote_average": 0,
        },
    ],
}


def test_an_episode_carries_its_rating_and_runtime():
    season = client(SEASON).season(1399, 1)

    first = season.episodes[0]
    assert first.rating == 6.6
    assert first.runtime == 66
    assert "/w780/still.jpg" in first.still_url


def test_an_unrated_episode_has_no_rating_and_no_runtime():
    season = client(SEASON).season(1399, 1)

    second = season.episodes[1]
    assert second.rating is None
    assert second.runtime == 0


# -- tmdb_backdrop_for_title() ----------------------------------------------

SHOWS = {
    "results": [
        {"media_type": "tv", "id": 1, "name": "W.W.D.D. Origins",
         "first_air_date": "2011-01-01"},
        {"media_type": "tv", "id": 2, "name": "W.W.D.D.",
         "first_air_date": "2011-09-01"},
    ]
}


@pytest.fixture(autouse=True)
def clear_title_cache():
    tmdb._TITLE_BACKDROPS.clear()
    yield
    tmdb._TITLE_BACKDROPS.clear()


def title_client(search, images=IMAGES, calls: list | None = None):
    def fetch(path, params):
        if calls is not None:
            calls.append(path)
        if path.endswith("/images"):
            return images
        return search

    return tmdb.TmdbClient(KEY, fetch=fetch)


def test_the_exact_title_is_what_supplies_the_backdrop():
    found = tmdb.tmdb_backdrop_for_title(
        "W.W.D.D.", "2011", client=title_client(SHOWS)
    )

    assert found.endswith("/w1280/textless.jpg")


def test_a_near_match_is_not_good_enough():
    only_near = {"results": [SHOWS["results"][0]]}

    found = tmdb.tmdb_backdrop_for_title(
        "W.W.D.D.", "2011", client=title_client(only_near)
    )

    assert found == ""


def test_a_title_two_years_off_is_refused():
    found = tmdb.tmdb_backdrop_for_title(
        "W.W.D.D.", "2014", client=title_client(SHOWS)
    )

    assert found == ""


def test_the_answer_is_cached_per_title():
    calls: list = []
    stub = title_client(SHOWS, calls=calls)

    first = tmdb.tmdb_backdrop_for_title("W.W.D.D.", "2011", client=stub)
    second = tmdb.tmdb_backdrop_for_title("W.W.D.D.", "2011", client=stub)

    assert first == second
    assert calls == ["/search/multi", "/tv/2/images"]
