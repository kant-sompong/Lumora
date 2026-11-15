"""The missing Frieren logo: an ani.pm row must reach TMDB's TV side.

ani.pm sends a title's episode count as an object (``{"total": 28, "sub": 28,
"dub": 28}``).  Read as a plain number it became zero, the GTK anime page read
"no episodes" as "a film", and the lookup went to TMDB's *movie* index, where
a series such as Frieren does not exist - so its logo was never even asked
for.  A same-named film (Death Note) hid the mistake.

Everything here stubs ``fetch``: no test touches the network.
"""

from __future__ import annotations

import types

import pytest

from lgl import anipm, movies_ui, tmdb

KEY = "0123456789abcdef0123456789abcdef"

#: The fields ani.pm actually returns for tv/209867, trimmed to the ones read.
FRIEREN_ROW = {
    "anilistId": 154587,
    "malId": 52991,
    "title": "Frieren: Beyond Journey\u2019s End",
    "format": "TV",
    "status": "Finished",
    "year": 2023,
    "score": 9.24,
    "episodes": {"total": 28, "sub": 28, "dub": 28},
    "genres": ["Adventure", "Drama", "Fantasy"],
    "adult": False,
    "poster": "https://ani.pm/api/anime/cover?anilistId=154587&malId=52991",
}

SEARCH = {
    "results": [
        {"media_type": "tv", "id": 209867, "name": "Frieren: Beyond Journey's End",
         "first_air_date": "2023-09-29"},
        {"media_type": "movie", "id": 1333101, "title": "Frieren",
         "release_date": "2024-01-05"},
    ]
}

#: TMDB's Japanese and Korean logos only - the report's case: no English one.
FOREIGN_LOGOS = {
    "logos": [
        {"file_path": "/frieren-ja.png", "iso_639_1": "ja", "vote_average": 6.0},
        {"file_path": "/frieren-ko.png", "iso_639_1": "ko", "vote_average": 9.0},
    ],
    "backdrops": [
        {"file_path": "/frieren-wide.jpg", "iso_639_1": None,
         "vote_average": 4.0, "aspect_ratio": 1.78},
    ],
}

ENGLISH_LOGO = {
    "logos": [{"file_path": "/death-note-en.png", "iso_639_1": "en",
               "vote_average": 5.0}],
    "backdrops": [],
}

NO_LOGOS = {"logos": [], "backdrops": []}


@pytest.fixture(autouse=True)
def clear_caches():
    tmdb._TITLE_IMAGES.clear()
    yield
    tmdb._TITLE_IMAGES.clear()


def client(
    payload, calls: list | None = None, results: dict | None = None
) -> tmdb.TmdbClient:
    """A client whose search answers *results* and whose images answer *payload*."""

    def fetch(path, params):
        if calls is not None:
            calls.append((path, dict(params)))
        return (results or SEARCH) if path == "/search/multi" else payload

    return tmdb.TmdbClient(KEY, fetch=fetch)


def tmdb_kind(item: object) -> str:
    """The GTK page's own answer, bound to a bare object."""
    return types.MethodType(
        movies_ui.MoviesWindow._anime_tmdb_kind, object()
    )(item)


def frieren() -> anipm.AniTitle:
    return anipm._parse_title(FRIEREN_ROW)


# -- reading the row --------------------------------------------------------


def test_the_episode_count_is_read_from_anipms_object():
    assert frieren().episodes == 28


def test_a_row_that_names_only_track_counts_still_has_a_count():
    row = dict(FRIEREN_ROW, episodes={"sub": 12, "dub": 5})
    assert anipm._parse_title(row).episodes == 12


def test_a_plain_episode_number_is_still_read():
    row = dict(FRIEREN_ROW, episodes=24)
    assert anipm._parse_title(row).episodes == 24


# -- which TMDB side the title asks ----------------------------------------


def test_a_series_row_asks_the_tv_side_even_with_no_episode_count():
    series = frieren()
    assert tmdb_kind(series) == "tv"
    assert tmdb_kind(anipm.AniTitle(anilist_id=1, title="Frieren")) == "tv"


def test_a_film_row_still_asks_the_movie_side():
    film = anipm._parse_title(dict(FRIEREN_ROW, title="Your Name.", format="MOVIE",
                                   year=2016, episodes={"total": 1}))
    assert tmdb_kind(film) == "movie"
    assert tmdb_kind(types.SimpleNamespace(media_type="movie")) == "movie"


# -- the logo that used to be missing --------------------------------------


def test_frieren_finds_a_logo_when_tmdb_has_only_foreign_ones():
    show = frieren()
    calls: list = []
    found = tmdb.tmdb_images_for_title(
        show.title, show.year, tmdb_kind(show), client=client(FOREIGN_LOGOS, calls),
        language="en",
    )

    # No English logo exists, so the best-voted available one is used.
    assert found.logo_url.endswith("/w500/frieren-ko.png")
    assert ("/tv/209867/images", {}) in calls


def test_the_images_request_asks_for_every_language():
    show = frieren()
    calls: list = []
    tmdb.tmdb_images_for_title(
        show.title, show.year, tmdb_kind(show), client=client(FOREIGN_LOGOS, calls),
        language="en",
    )

    asked = dict(calls)[  # the images read, not the search
        "/tv/209867/images"
    ]
    assert asked == {}


def test_death_notes_english_logo_is_still_the_one_shown():
    show = anipm._parse_title(dict(FRIEREN_ROW, title="Death Note", year=2006,
                                   anilistId=1535))
    death_note = {
        "results": [
            {"media_type": "tv", "id": 13916, "name": "Death Note",
             "first_air_date": "2006-10-04"},
        ]
    }
    found = tmdb.tmdb_images_for_title(
        show.title, show.year, tmdb_kind(show),
        client=client(ENGLISH_LOGO, results=death_note), language="en",
    )

    assert found.logo_url.endswith("/w500/death-note-en.png")


def test_a_title_without_logos_keeps_its_text_title():
    show = frieren()
    found = tmdb.tmdb_images_for_title(
        show.title, show.year, tmdb_kind(show), client=client(NO_LOGOS),
        language="en",
    )

    assert found == tmdb.MediaImages()
    assert found.backdrop_url == ""
