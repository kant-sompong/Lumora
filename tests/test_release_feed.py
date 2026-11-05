"""Release calendar data: parsing, failure handling and the cache (no network)."""

import json
from datetime import datetime, timezone

import pytest

from lgl import anime_calendar as cal
from lgl import release_feed as feed

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc).timestamp()


def anilist_page(rows, more=False):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": more}, "airingSchedules": rows}}}


def row(ident, title, episode, at, adult=False):
    return {"airingAt": at, "episode": episode,
            "media": {"id": ident, "isAdult": adult, "title": {"english": title, "romaji": "r" + title}}}


def test_anime_rows_become_entries_and_adult_or_broken_rows_are_skipped():
    page = anilist_page([
        row(1, "Alpha", 3, NOW + 3600),
        row(2, "Hidden", 1, NOW + 7200, adult=True),
        {"airingAt": NOW, "media": None},
        "junk",
        row(3, "", 2, NOW + 60),
    ])
    entries = feed.fetch_anime(now=NOW, days=7, post=lambda body: page)
    keys = [e.key for e in entries]
    assert "anilist:2" not in keys and "anilist:1" in keys
    assert [(e.key, e.title, e.episode) for e in entries if e.key == "anilist:1"] == [("anilist:1", "Alpha", "3")]
    assert all(e.known and not e.date_only for e in entries)


def test_anime_follows_pagination_and_stops():
    calls = []

    def post(body):
        calls.append(body["variables"]["page"])
        return anilist_page([row(calls[-1], f"T{calls[-1]}", 1, NOW + 60)], more=calls[-1] < 2)

    assert len(feed.fetch_anime(now=NOW, days=3, post=post)) == 2
    assert calls == [1, 2]


def test_anime_rejects_a_document_that_is_not_a_page():
    with pytest.raises(ValueError):
        feed.fetch_anime(now=NOW, days=3, post=lambda body: {"errors": ["rate limited"]})


def tmdb_get(path, params):
    if path == "/discover/movie":
        return {"results": [{"id": 9, "title": "Film", "release_date": "2026-11-12"}, {"id": 10, "title": "No date"}]}
    if path == "/tv/on_the_air":
        return {"results": [{"id": 5, "name": "Show"}, {"id": 6, "name": "Idle"}, {"id": 7, "name": "Broken"}]}
    if path == "/tv/5":
        return {"next_episode_to_air": {"air_date": "2026-11-07", "season_number": 2, "episode_number": 4}}
    if path == "/tv/6":
        return {"next_episode_to_air": None}
    raise OSError("boom")


def test_movies_and_tv_are_date_only_and_failures_are_skipped():
    movies = feed.fetch_movies(now=NOW, days=14, get=tmdb_get)
    assert [(e.key, e.date_only) for e in movies] == [("tmdb:movie:9", True)]
    tv = feed.fetch_tv(now=NOW, days=14, get=tmdb_get)
    assert [(e.key, e.episode) for e in tv] == [("tmdb:tv:5", "S2E4")]
    assert feed.kind_of(movies[0]) == "movie" and feed.kind_of(tv[0]) == "tv"


def test_a_date_is_shown_without_a_time_and_is_not_moved_by_timezone():
    entry = feed.fetch_movies(now=NOW, days=14, get=tmdb_get)[0]
    assert cal.format_when(entry, "Pacific/Auckland") == "Thu 12 Nov"
    assert cal.format_when(entry, "America/Los_Angeles") == "Thu 12 Nov"
    assert cal.grouped([entry], "Asia/Bangkok")[0][0] == "Thursday 12 November"


def test_local_zone_name_is_accepted():
    assert cal.zone("local") is not None
    assert cal.zone("nonsense") is not None


def test_cache_is_used_until_it_expires_and_survives_a_total_failure(tmp_path):
    path = tmp_path / "cache.json"
    ok = lambda body: anilist_page([row(1, "Alpha", 1, NOW + 60)])  # noqa: E731
    first = feed.load_feed(path, now=NOW, anilist=ok)
    assert [e.key for e in first.entries] == ["anilist:1"] and not first.from_cache

    def never(body):
        raise AssertionError("must not fetch while the cache is fresh")

    assert feed.load_feed(path, now=NOW + 60, anilist=never).from_cache

    def down(body):
        raise OSError("offline")

    stale = feed.load_feed(path, now=NOW + feed.CACHE_TTL + 1, anilist=down)
    assert stale.from_cache and [e.key for e in stale.entries] == ["anilist:1"]
    assert any("offline" in message for message in stale.errors)


def test_without_a_tmdb_key_anime_still_loads_and_the_user_is_told(tmp_path):
    result = feed.load_feed(tmp_path / "c.json", now=NOW, anilist=lambda b: anilist_page([row(1, "A", 1, NOW + 5)]))
    assert len(result.entries) == 1
    assert any("TMDB key" in message for message in result.errors)


def test_a_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    result = feed.load_feed(path, now=NOW, anilist=lambda b: anilist_page([]))
    assert result.entries == [] and not result.from_cache
    assert json.loads(path.read_text())["entries"] == []


def test_select_filters_by_kind_and_follows_and_sorts():
    a = cal.Entry("anilist:1", "Zed", air_at=NOW + 10)
    b = cal.Entry("tmdb:tv:2", "Beta", air_at=NOW + 5, date_only=True)
    c = cal.Entry("tmdb:movie:3", "Cee", air_at=None)
    entries = [a, c, b]
    assert [e.key for e in feed.select(entries, "all")] == ["tmdb:tv:2", "anilist:1", "tmdb:movie:3"]
    assert [e.key for e in feed.select(entries, "anime")] == ["anilist:1"]
    assert [e.key for e in feed.select(entries, "movie")] == ["tmdb:movie:3"]
    assert [e.key for e in feed.select(entries, "following", ["tmdb:tv:2"])] == ["tmdb:tv:2"]
