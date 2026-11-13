"""Parsing and caching for the ratings row.  No network, no display."""

from __future__ import annotations

import json

import pytest

from lgl import ratings
from lgl.ratings import (
    CACHE_TTL,
    OMDB_ENV,
    OMDB_SETTING,
    Ratings,
    fetch_ratings,
    parse_omdb,
    read_omdb_key,
    tmdb_percent,
)

PAYLOAD = {
    "Response": "True",
    "imdbRating": "8.5",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.5/10"},
        {"Source": "Rotten Tomatoes", "Value": "90%"},
        {"Source": "Metacritic", "Value": "88/100"},
    ],
}


class Fetcher:
    def __init__(self, payload=PAYLOAD, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        if self.fail:
            raise TimeoutError("OMDb did not answer")
        return self.payload


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def test_parse_omdb_reads_both_scores():
    result = parse_omdb(PAYLOAD)

    assert result.imdb == 8.5
    assert result.rotten == 90
    assert result.tmdb_percent is None


def test_parse_omdb_accepts_a_slash_rating():
    assert parse_omdb({"imdbRating": "8.5/10"}).imdb == 8.5


def test_parse_omdb_treats_na_as_no_data():
    result = parse_omdb({
        "imdbRating": "N/A",
        "Ratings": [{"Source": "Rotten Tomatoes", "Value": "N/A"}],
    })

    assert result == Ratings()


def test_parse_omdb_tolerates_missing_and_malformed_payloads():
    assert parse_omdb({}) == Ratings()
    assert parse_omdb(None) == Ratings()
    assert parse_omdb("nonsense") == Ratings()
    assert parse_omdb({"Response": "False", "Error": "Movie not found!"}) == Ratings()
    assert parse_omdb({"imdbRating": "eleven", "Ratings": "not a list"}) == Ratings()
    assert parse_omdb({"imdbRating": "11.0"}).imdb is None
    assert parse_omdb({"Ratings": [{"Source": "Rotten Tomatoes", "Value": "900%"}]}).rotten is None


def test_parse_omdb_ignores_a_non_rotten_tomatoes_source():
    payload = {"Ratings": [{"Source": "Metacritic", "Value": "88/100"}]}
    assert parse_omdb(payload).rotten is None


# --------------------------------------------------------------------------
# TMDB percentage
# --------------------------------------------------------------------------


def test_tmdb_percent_rounds_the_average():
    assert tmdb_percent(6.4, 120) == 64
    assert tmdb_percent(8.55, 10) == 86
    assert tmdb_percent(7.0, 1) == 70


def test_tmdb_percent_needs_real_votes():
    assert tmdb_percent(7.5, 0) is None
    assert tmdb_percent(0, 50) is None
    assert tmdb_percent(None, 50) is None
    assert tmdb_percent("nonsense", 50) is None


# --------------------------------------------------------------------------
# fetching and caching
# --------------------------------------------------------------------------


def test_a_successful_fetch_is_cached_for_seven_days(tmp_path):
    fetcher = Fetcher()
    first = fetch_ratings(
        "tt0133093", 7.9, 22000, key="abc", fetcher=fetcher, cache_dir=tmp_path, now=1000.0
    )
    assert first == Ratings(imdb=8.5, rotten=90, tmdb_percent=79)
    assert len(fetcher.urls) == 1
    assert "www.omdbapi.com" in fetcher.urls[0]
    assert "tt0133093" in fetcher.urls[0]

    again = fetch_ratings(
        "tt0133093", 7.9, 22000, key="abc", fetcher=fetcher, cache_dir=tmp_path,
        now=1000.0 + CACHE_TTL - 1,
    )
    assert again == first
    assert len(fetcher.urls) == 1  # served from the cache


def test_the_cache_expires_after_seven_days(tmp_path):
    fetcher = Fetcher()
    fetch_ratings("tt0133093", 7.9, 22000, key="abc", fetcher=fetcher, cache_dir=tmp_path, now=0.0)
    fetch_ratings(
        "tt0133093", 7.9, 22000, key="abc", fetcher=fetcher, cache_dir=tmp_path,
        now=CACHE_TTL + 1,
    )

    assert len(fetcher.urls) == 2
    assert json.loads((tmp_path / ratings.CACHE_NAME).read_text())["tt0133093"]


def test_a_failed_fetch_returns_the_tmdb_score_and_never_raises(tmp_path):
    result = fetch_ratings(
        "tt0133093", 6.4, 500, key="abc", fetcher=Fetcher(fail=True),
        cache_dir=tmp_path, now=0.0,
    )

    assert result == Ratings(tmdb_percent=64)


def test_no_key_still_reports_the_tmdb_score(tmp_path):
    fetcher = Fetcher()

    result = fetch_ratings(
        "tt0133093", 6.4, 500, key="", fetcher=fetcher, cache_dir=tmp_path, now=0.0
    )

    assert result == Ratings(tmdb_percent=64)
    assert fetcher.urls == []


def test_no_imdb_id_only_reports_the_tmdb_score(tmp_path):
    fetcher = Fetcher()

    result = fetch_ratings(
        "", 6.4, 500, key="abc", fetcher=fetcher, cache_dir=tmp_path, now=0.0
    )

    assert result == Ratings(tmdb_percent=64)
    assert fetcher.urls == []


def test_an_unusable_omdb_answer_is_not_cached(tmp_path):
    fetcher = Fetcher({"Response": "False", "Error": "Movie not found!"})

    result = fetch_ratings(
        "tt0000001", 6.4, 500, key="abc", fetcher=fetcher, cache_dir=tmp_path, now=0.0
    )

    assert result == Ratings(tmdb_percent=64)
    assert not (tmp_path / ratings.CACHE_NAME).exists()


def test_a_corrupt_cache_file_is_ignored(tmp_path):
    (tmp_path / ratings.CACHE_NAME).write_text("{not json", encoding="utf-8")
    fetcher = Fetcher()

    result = fetch_ratings(
        "tt0133093", 8.0, 100, key="abc", fetcher=fetcher, cache_dir=tmp_path, now=5.0
    )

    assert result.imdb == 8.5
    assert len(fetcher.urls) == 1


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


class Settings:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_the_environment_key_wins():
    settings = Settings({OMDB_SETTING: "from-settings"})

    assert read_omdb_key(settings, {OMDB_ENV: "from-env"}) == "from-env"
    assert read_omdb_key(settings, {}) == "from-settings"
    assert read_omdb_key(Settings(), {}) == ""


def test_the_setting_name_is_the_contract_name():
    assert OMDB_ENV == "OMDB_API_KEY"
    assert OMDB_SETTING == "omdb_api_key"


def test_a_settings_failure_is_not_an_error():
    class Broken:
        def get(self, key, default=None):
            raise RuntimeError("settings are unreadable")

    assert read_omdb_key(Broken(), {}) == ""


@pytest.mark.parametrize("value", ["", " ", None])
def test_a_blank_key_is_no_key(value):
    assert read_omdb_key(Settings({OMDB_SETTING: value}), {}) == ""
