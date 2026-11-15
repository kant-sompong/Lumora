"""Title logos: which one is picked, in which language, and what is cached.

Everything here stubs ``fetch``, so no test touches the network and none needs
a real key.
"""

from __future__ import annotations

import pytest

from lgl import tmdb

KEY = "0123456789abcdef0123456789abcdef"

BACKDROPS = [
    {"file_path": "/textless.jpg", "iso_639_1": None,
     "vote_average": 4.0, "aspect_ratio": 1.78},
]


def client(payload, calls: list | None = None) -> tmdb.TmdbClient:
    """A client whose one answer is ``payload`` (or the error it raises)."""

    def fetch(path, params):
        if calls is not None:
            calls.append((path, dict(params)))
        if isinstance(payload, Exception):
            raise payload
        return payload

    return tmdb.TmdbClient(KEY, fetch=fetch)


def logo(path, language, votes=5.0):
    return {"file_path": path, "iso_639_1": language, "vote_average": votes}


def images(*logos, backdrops=BACKDROPS):
    return {"logos": list(logos), "backdrops": list(backdrops)}


# -- which logo, in which language ------------------------------------------


def test_the_requested_language_wins_over_english_and_neutral():
    found = client(images(
        logo("/neutral.png", None, 9.0),
        logo("/english.png", "en", 9.0),
        logo("/french.png", "fr", 1.0),
    )).images("tv", 1399, language="fr")

    assert found.logo_url.endswith("/w500/french.png")


def test_english_is_used_when_the_requested_language_has_no_logo():
    found = client(images(
        logo("/neutral.png", None, 9.0),
        logo("/english.png", "en", 5.0),
    )).images("tv", 1399, language="fr")

    assert found.logo_url.endswith("/w500/english.png")


def test_a_language_neutral_logo_is_the_last_resort():
    found = client(images(
        logo("/neutral.png", None, 1.0),
        logo("/french.png", "fr", 9.0),
    )).images("movie", 603, language="de")

    assert found.logo_url.endswith("/w500/neutral.png")


def test_a_full_locale_tag_is_read_as_its_language():
    found = client(images(logo("/french.png", "fr", 1.0))).images(
        "tv", 1399, language="fr-CA"
    )

    assert found.logo_url.endswith("/w500/french.png")


def test_the_language_is_sent_to_the_images_endpoint():
    calls: list = []
    client(images(logo("/french.png", "fr")), calls).images(
        "tv", 1399, language="fr"
    )

    assert calls == [(
        "/tv/1399/images",
        {"language": "en-US", "include_image_language": "fr,en,null"},
    )]


# -- what counts as a logo --------------------------------------------------


def test_a_png_beats_a_better_voted_svg_in_the_same_language():
    found = client(images(
        logo("/art.svg", "en", 9.0),
        logo("/art.png", "en", 1.0),
    )).images("tv", 1399, language="en")

    assert found.logo_url.endswith("/w500/art.png")


def test_a_photo_is_not_a_logo():
    found = client(images(logo("/photo.jpg", "en", 9.0))).images(
        "tv", 1399, language="en"
    )

    assert found.logo_url == ""


def test_a_title_without_logos_gets_none():
    found = client(images()).images("tv", 1399)

    assert found.logo_url == ""


def test_a_failed_request_gets_none_rather_than_raising():
    found = client(tmdb.TmdbError("TMDB answered HTTP 500.")).images("tv", 1399)

    assert found.logo_url == ""


# -- both kinds, and the cache ----------------------------------------------


def test_a_movie_and_a_series_use_their_own_endpoints():
    calls: list = []
    stub = client(images(logo("/english.png", "en")), calls)

    stub.images("movie", 603)
    stub.images("tv", 1399)

    assert [path for path, _params in calls] == [
        "/movie/603/images", "/tv/1399/images",
    ]


def test_the_logo_answer_is_cached_per_title_and_language():
    calls: list = []
    stub = client(images(
        logo("/english.png", "en", 9.0),
        logo("/french.png", "fr", 1.0),
    ), calls)

    first = stub.images("tv", 1399, language="en")
    again = stub.images("tv", 1399, language="en")
    french = stub.images("tv", 1399, language="fr")

    assert first.logo_url == again.logo_url
    assert french.logo_url.endswith("/w500/french.png")
    assert len(calls) == 2


# -- finding a title's art by name ------------------------------------------

SHOWS = {
    "results": [
        {"media_type": "tv", "id": 1, "name": "Frieren: Beyond Journey's End",
         "first_air_date": "2023-09-29"},
        {"media_type": "movie", "id": 2, "name": "Frieren",
         "release_date": "2024-01-05"},
    ]
}


@pytest.fixture(autouse=True)
def clear_title_cache():
    tmdb._TITLE_IMAGES.clear()
    yield
    tmdb._TITLE_IMAGES.clear()


def title_client(images_payload, calls: list | None = None):
    def fetch(path, params):
        if calls is not None:
            calls.append(path)
        if path.endswith("/images"):
            return images_payload
        return SHOWS

    return tmdb.TmdbClient(KEY, fetch=fetch)


def test_a_title_lookup_brings_the_logo_and_the_backdrop_at_once():
    found = tmdb.tmdb_images_for_title(
        "Frieren", "2024", "movie",
        client=title_client(images(
            logo("/neutral.png", None), logo("/english.png", "en"),
        )),
    )

    assert found.logo_url.endswith("/w500/english.png")
    assert found.backdrop_url.endswith("/w1280/textless.jpg")


def test_a_title_lookup_asks_for_the_display_language():
    calls: list = []
    stub = title_client(images(
        logo("/english.png", "en", 9.0), logo("/french.png", "fr", 1.0),
    ), calls)

    found = tmdb.tmdb_images_for_title(
        "Frieren", "2024", "movie", client=stub, language="fr"
    )

    assert found.logo_url.endswith("/w500/french.png")
    assert calls == ["/search/multi", "/movie/2/images"]


def test_a_title_lookup_is_cached_so_reopening_asks_nothing():
    calls: list = []
    stub = title_client(images(logo("/english.png", "en")), calls)

    first = tmdb.tmdb_images_for_title("Frieren", "2024", "movie", client=stub)
    second = tmdb.tmdb_images_for_title("Frieren", "2024", "movie", client=stub)

    assert first is second
    assert calls == ["/search/multi", "/movie/2/images"]


def test_a_near_title_match_gets_no_art_at_all():
    found = tmdb.tmdb_images_for_title(
        "Frieren", "2024", "tv", client=title_client(images()),
    )

    assert found.logo_url == ""
    assert found.backdrop_url == ""
