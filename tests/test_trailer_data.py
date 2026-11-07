"""Trailer selection, URL safety, cache and the autoplay default, all offline."""

from __future__ import annotations

import json
import urllib.error

import pytest

from lgl import trailer_data, trailers

VIDEOS = [
    {"site": "YouTube", "type": "Teaser", "key": "teaser00001", "name": "Official Teaser",
     "official": True, "published_at": "2024-01-01T00:00:00.000Z"},
    {"site": "Vimeo", "type": "Trailer", "key": "https://evil.example/x", "name": "Vimeo cut",
     "official": True},
    {"site": "YouTube", "type": "Trailer", "key": "unofficial01", "name": "Fan trailer",
     "official": False, "published_at": "2024-05-01T00:00:00.000Z"},
    {"site": "YouTube", "type": "Trailer", "key": "official001", "name": "Official Trailer",
     "official": True, "published_at": "2024-02-01T00:00:00.000Z"},
    {"site": "YouTube", "type": "Featurette", "key": "featurette01", "name": "Featurette",
     "official": True},
]


class Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)


def payload(rows):
    return {"id": 1, "results": rows}


def test_the_official_trailer_wins_over_a_teaser_or_an_unofficial_cut():
    chosen = trailer_data.pick_trailer(VIDEOS)
    assert chosen is not None
    assert chosen["key"] == "official001"
    assert chosen["type"] == "Trailer"
    assert chosen["official"] is True


def test_a_teaser_is_used_when_there_is_no_trailer():
    chosen = trailer_data.pick_trailer([VIDEOS[0]])
    assert chosen is not None and chosen["type"] == "Teaser"


def test_non_youtube_sites_types_and_bad_keys_are_rejected():
    rows = [
        {"site": "Vimeo", "type": "Trailer", "key": "abcdefghijk", "official": True},
        {"site": "YouTube", "type": "Featurette", "key": "abcdefghijk", "official": True},
        {"site": "YouTube", "type": "Trailer", "key": "https://evil.example/t", "official": True},
        {"site": "YouTube", "type": "Trailer", "key": "abc", "official": True},
        "junk",
    ]
    assert trailer_data.pick_trailer(rows) is None


def test_the_embed_url_is_always_youtube_nocookie():
    assert trailer_data.embed_url("dQw4w9WgXcQ") == (
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    )
    assert trailer_data.embed_url("https://evil.example/x") == ""
    assert trailer_data.embed_url("javascript:alert(1)") == ""
    assert trailer_data.embed_url("../../evil") == ""


def test_autoplay_only_adds_the_mute_parameters_when_asked():
    base = trailer_data.embed_url("dQw4w9WgXcQ")
    assert "autoplay" not in base and "mute" not in base
    assert trailer_data.embed_url("dQw4w9WgXcQ", autoplay=True) == (
        base + "?autoplay=1&mute=1"
    )
    assert trailer_data.embed_url("dQw4w9WgXcQ", autoplay=True, muted=False) == (
        base + "?autoplay=1"
    )


def test_trailers_never_autoplay_by_default():
    assert trailers.autoplay_allowed(None) is False
    assert trailers.autoplay_allowed(object()) is False
    assert trailers.autoplay_allowed(Settings()) is False
    assert trailers.autoplay_allowed(Settings({"trailer_autoplay": True})) is True
    assert trailers.muted_default(None) is True


def test_load_reads_the_videos_endpoint_and_caches_for_a_day(tmp_path):
    cache = tmp_path / "trailers.json"
    seen = []

    def fetch(path):
        seen.append(path)
        return payload(VIDEOS)

    first = trailer_data.load_trailer("movie", 603, fetch=fetch, now=1000, cache_path=cache)
    assert seen == ["/movie/603/videos"]
    assert first.trailer and first.trailer["key"] == "official001"
    assert first.error_text == ""
    assert first.from_cache is False
    assert cache.is_file()

    def explode(_path):
        raise AssertionError("the cache should have answered this")

    second = trailer_data.load_trailer("movie", 603, fetch=explode, now=1000 + 60,
                                       cache_path=cache)
    assert second.from_cache is True
    assert second.trailer and second.trailer["key"] == "official001"


def test_an_expired_cache_asks_tmdb_again(tmp_path):
    cache = tmp_path / "trailers.json"
    trailer_data.load_trailer("tv", 9, fetch=lambda _p: payload(VIDEOS), now=1000,
                              cache_path=cache)
    fresh = {"results": [{"site": "YouTube", "type": "Trailer", "key": "newtrailer1",
                          "name": "New", "official": True}]}
    again = trailer_data.load_trailer(
        "tv", 9, fetch=lambda _p: payload(fresh["results"]), now=1000 + trailer_data.CACHE_TTL + 1,
        cache_path=cache,
    )
    assert again.from_cache is False
    assert again.trailer and again.trailer["key"] == "newtrailer1"


def test_a_title_without_a_youtube_trailer_has_no_trailer_and_no_error(tmp_path):
    result = trailer_data.load_trailer(
        "movie", 1, fetch=lambda _p: payload([VIDEOS[1]]), cache_path=tmp_path / "t.json"
    )
    assert result.trailer is None
    assert result.has_trailer is False
    assert result.error_text == ""


def test_a_timeout_is_one_readable_line(tmp_path):
    def slow(_path):
        raise TimeoutError("timed out")

    result = trailer_data.load_trailer("tv", 9, fetch=slow, cache_path=tmp_path / "t.json")
    assert result.trailer is None
    assert result.error_text == "Could not load: TMDB (/tv/9/videos): the request timed out"


def test_a_failed_refresh_keeps_the_cached_trailer(tmp_path):
    cache = tmp_path / "trailers.json"
    trailer_data.load_trailer("movie", 603, fetch=lambda _p: payload(VIDEOS), now=1000,
                              cache_path=cache)

    def offline(_path):
        raise urllib.error.URLError("network down")

    stale = trailer_data.load_trailer(
        "movie", 603, fetch=offline, now=1000 + trailer_data.CACHE_TTL + 1, cache_path=cache
    )
    assert stale.from_cache is True
    assert stale.trailer and stale.trailer["key"] == "official001"
    assert "network error" in stale.error


def test_a_cached_url_can_never_point_away_from_youtube_nocookie(tmp_path):
    cache = tmp_path / "trailers.json"
    cache.write_text(json.dumps({"version": 1, "videos": {"movie:5": {
        "fetched_at": 1000,
        "trailer": {"key": "dQw4w9WgXcQ", "name": "Cached", "type": "Trailer",
                    "url": "https://evil.example/watch"},
    }}}), "utf-8")

    def explode(_path):
        raise AssertionError("fresh cache should answer")

    result = trailer_data.load_trailer("movie", 5, fetch=explode, now=1000 + 10,
                                       cache_path=cache)
    assert result.trailer and result.trailer["key"] == "dQw4w9WgXcQ"
    assert "evil" not in result.embed()
    assert result.embed().startswith("https://www.youtube-nocookie.com/embed/")

    cache.write_text(json.dumps({"version": 1, "videos": {"movie:6": {
        "fetched_at": 1000,
        "trailer": {"key": "https://evil.example/x", "name": "Bad"},
    }}}), "utf-8")
    bad = trailer_data.load_trailer("movie", 6, fetch=explode, now=1000 + 10,
                                    cache_path=cache)
    assert bad.trailer is None


def test_a_title_without_a_usable_id_says_so():
    result = trailer_data.load_trailer("anime", 0)
    assert result.error_text.startswith("Could not load: ")
