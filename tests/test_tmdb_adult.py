"""Adult titles: searched for, flagged on the rows, and never served stale.

Every test stubs ``fetch``, so nothing here touches the network or needs a key.
"""

from __future__ import annotations

import pytest

from lgl import tmdb

KEY = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def clear_cache():
    tmdb._TITLE_IMAGES.clear()
    yield
    tmdb._TITLE_IMAGES.clear()


def recording_client(payload, calls: list) -> tmdb.TmdbClient:
    """A client that records (path, params) and answers ``payload``."""

    def fetch(path, params):
        calls.append((path, dict(params)))
        return payload

    return tmdb.TmdbClient(KEY, fetch=fetch)


def search_client(results, images=None, calls: list | None = None):
    """A client whose search answers ``results`` and images answers ``images``."""

    def fetch(path, params):
        if calls is not None:
            calls.append((path, dict(params)))
        if path == "/search/multi":
            return {"results": list(results)}
        return images or {"logos": [], "backdrops": []}

    return tmdb.TmdbClient(KEY, fetch=fetch)


def tv_entry(tmdb_id=95479, title="Overflow", year="2020", *, adult=True, lang="ja"):
    return {
        "media_type": "tv", "id": tmdb_id, "name": title,
        "first_air_date": f"{year}-01-05", "original_language": lang,
        "genre_ids": [16], "adult": adult, "vote_average": 6.0,
    }


# -- the request ------------------------------------------------------------


def test_every_search_sends_include_adult_true():
    calls: list = []
    recording_client({"results": []}, calls).search("overflow")

    path, params = calls[0]
    assert path == "/search/multi"
    assert params["query"] == "overflow"
    # TMDB wants the flag as the string "true", not a JSON boolean.
    assert params["include_adult"] == "true"
    assert params["page"] == 1


def test_a_later_search_page_also_asks_for_adult_titles():
    calls: list = []
    recording_client({"results": []}, calls).search("overflow", page=3)

    assert calls[0][1]["include_adult"] == "true"


def test_the_title_lookup_search_inherits_the_same_flag():
    calls: list = []
    tmdb.tmdb_images_for_title(
        "Overflow", "2020", "tv", client=search_client([tv_entry()], calls=calls)
    )

    assert calls[0][0] == "/search/multi"
    assert calls[0][1]["include_adult"] == "true"


# -- the flag on a row ------------------------------------------------------

def test_an_adult_row_is_flagged():
    rows = tmdb.TmdbClient._items([tv_entry(adult=True)], default_kind="")

    assert rows[0].adult is True


def test_a_regular_row_is_not_flagged():
    rows = tmdb.TmdbClient._items([tv_entry(adult=False)], default_kind="")

    assert rows[0].adult is False


def test_a_row_without_the_field_defaults_to_not_adult():
    raw = tv_entry()
    raw.pop("adult")

    assert tmdb.TmdbClient._items([raw], default_kind="")[0].adult is False


def test_a_string_is_not_read_as_true():
    # TMDB sends a JSON boolean; anything else is a payload quirk, not a flag.
    rows = tmdb.TmdbClient._items([tv_entry(adult="true")], default_kind="")

    assert rows[0].adult is False


def test_the_media_item_default_keeps_stubs_working():
    assert tmdb.MediaItem(kind="movie", tmdb_id=1, title="A Film").adult is False


# -- the cache --------------------------------------------------------------


def test_results_cached_under_the_old_filter_are_not_served(monkeypatch):
    calls: list = []
    stub = search_client([tv_entry()], calls=calls)

    monkeypatch.setattr(tmdb, "INCLUDE_ADULT", "false")
    tmdb.tmdb_images_for_title("Overflow", "2020", "tv", client=stub)
    monkeypatch.setattr(tmdb, "INCLUDE_ADULT", "true")
    tmdb.tmdb_images_for_title("Overflow", "2020", "tv", client=stub)

    # The wider search is a different question, so it is asked again instead
    # of being answered from the results the filtered search cached.
    assert [path for path, _ in calls].count("/search/multi") == 2
    assert len(tmdb._TITLE_IMAGES) == 2


def test_the_same_filter_still_hits_the_cache():
    calls: list = []
    stub = search_client([tv_entry()], calls=calls)

    first = tmdb.tmdb_images_for_title("Overflow", "2020", "tv", client=stub)
    second = tmdb.tmdb_images_for_title("Overflow", "2020", "tv", client=stub)

    assert first is second
    assert [path for path, _ in calls].count("/search/multi") == 1
