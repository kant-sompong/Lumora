"""Season fetching, caching and comparison, with no network and no GUI.

``post`` is a stub, so every test is offline; the canned payloads mirror the
shape of AniList's ``Page(media(season:…))`` answer.
"""

from __future__ import annotations

import calendar
import urllib.error

import pytest

from lgl import season_data


def media(ident: int, english: str = "", romaji: str = "", score=80, episodes=12,
          fmt="TV", status="FINISHED"):
    return {
        "id": ident,
        "title": {"english": english, "romaji": romaji or english},
        "averageScore": score,
        "episodes": episodes,
        "format": fmt,
        "status": status,
    }


def page(rows, *, has_next: bool = False):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": has_next}, "media": rows}}}


WINTER_ROWS = [
    media(1, "Frieren", score=90, episodes=28),
    media(2, "Solo Leveling", score=82),
]
SPRING_ROWS = [
    media(2, "Solo Leveling", score=82),          # same series id, carried over
    media(3, "Kaiju No. 8", score=78),
]


def winter_post(*_args, **_kwargs):
    return page(WINTER_ROWS)


def spring_post(*_args, **_kwargs):
    return page(SPRING_ROWS)


def by_season(winter=winter_post, spring=spring_post):
    def post(body):
        sender = spring if body["variables"]["season"] == "SPRING" else winter
        return sender(body)
    return post


def test_fetch_season_merges_pages_and_shapes_the_entries():
    calls = []

    def post(body):
        calls.append(body)
        if body["variables"]["page"] == 1:
            return page([WINTER_ROWS[0]], has_next=True)
        return page([WINTER_ROWS[1]])

    entries = season_data.fetch_season("winter", "2024", post=post)

    assert [entry["key"] for entry in entries] == ["anilist:1", "anilist:2"]
    assert entries[0]["title"] == "Frieren"
    assert entries[0]["season"] == "winter"
    assert entries[0]["year"] == 2024
    assert entries[0]["season_key"] == "2024 winter"
    assert entries[0]["score"] == 90
    assert entries[0]["episodes"] == 28
    assert entries[0]["source"] == "AniList"
    assert calls[0]["variables"] == {"season": "WINTER", "seasonYear": 2024, "page": 1}
    assert "media(season: $season, seasonYear: $seasonYear, type: ANIME" in calls[0]["query"]


def test_fetch_season_skips_unusable_rows_and_falls_back_to_romaji():
    def post(_body):
        return page([
            media(0, "No id"),                       # id must be positive
            {"id": 7, "title": {"romaji": "One Piece"}},
            {"id": 8, "title": {"english": ""}},
            "not a row",
        ])

    entries = season_data.fetch_season("FALL", 1999, post=post)
    assert [entry["key"] for entry in entries] == ["anilist:7"]
    assert entries[0]["title"] == "One Piece"
    assert entries[0]["score"] is None and entries[0]["episodes"] is None


def test_fetch_season_surfaces_an_anilist_error_message():
    def post(_body):
        return {"errors": [{"message": "Too many requests"}]}

    with pytest.raises(ValueError, match="Too many requests"):
        season_data.fetch_season("WINTER", 2024, post=post)


def test_a_fresh_cache_is_used_without_touching_the_network(tmp_path):
    cache = tmp_path / "season-compare.json"
    first = season_data.load_season("WINTER", 2024, post=winter_post, now=1000,
                                    cache_path=cache)
    assert first.from_cache is False
    assert cache.is_file()

    def explode(_body):
        raise AssertionError("the cache should have answered this")

    second = season_data.load_season("WINTER", 2024, post=explode, now=1000 + 60,
                                     cache_path=cache)
    assert second.from_cache is True
    assert [entry["key"] for entry in second.entries] == ["anilist:1", "anilist:2"]


def test_an_expired_cache_is_fetched_again(tmp_path):
    cache = tmp_path / "season-compare.json"
    season_data.load_season("WINTER", 2024, post=winter_post, now=1000, cache_path=cache)

    def newer(_body):
        return page([media(9, "New Winter Show")])

    fresh = season_data.load_season(
        "WINTER", 2024, post=newer, now=1000 + season_data.CACHE_TTL + 1,
        cache_path=cache,
    )
    assert fresh.from_cache is False
    assert [entry["title"] for entry in fresh.entries] == ["New Winter Show"]


def test_a_failed_refresh_falls_back_to_saved_titles_with_a_message(tmp_path):
    cache = tmp_path / "season-compare.json"
    season_data.load_season("WINTER", 2024, post=winter_post, now=1000, cache_path=cache)

    def offline(_body):
        raise urllib.error.URLError("Name or service not known")

    stale = season_data.load_season(
        "WINTER", 2024, post=offline, now=1000 + season_data.CACHE_TTL + 1,
        cache_path=cache,
    )
    assert stale.from_cache is True
    assert len(stale.entries) == 2
    assert "network error" in stale.error
    assert "Winter 2024" in stale.error


def test_a_timeout_is_a_plain_message_not_an_exception(tmp_path):
    def slow(_body):
        raise TimeoutError("timed out")

    result = season_data.load_season("WINTER", 2024, post=slow, cache_path=tmp_path / "c.json")
    assert result.entries == []
    assert result.error == "AniList (Winter 2024): the request timed out"


def test_an_unknown_season_or_year_is_explained(tmp_path):
    result = season_data.load_season("MONSOON", 2024, post=winter_post,
                                     cache_path=tmp_path / "c.json")
    assert result.entries == []
    assert "Unknown season" in result.error

    other = season_data.load_season("WINTER", "not a year", post=winter_post,
                                    cache_path=tmp_path / "c.json")
    assert "Unknown season" in other.error


def test_compare_splits_only_and_shared_titles(tmp_path):
    result = season_data.compare(
        "WINTER", 2024, "SPRING", 2024,
        post=by_season(), cache_path=tmp_path / "season-compare.json",
    )

    assert [entry["title"] for entry in result.only_first] == ["Frieren"]
    assert [entry["title"] for entry in result.only_second] == ["Kaiju No. 8"]
    assert [entry["title"] for entry in result.shared] == ["Solo Leveling"]
    assert result.summary == "2024 winter: 2 titles, 2024 spring: 2 titles, 1 in both"
    assert result.errors == []
    assert result.error_text == ""


def test_compare_reports_which_side_failed(tmp_path):
    def offline(_body):
        raise TimeoutError("timed out")

    result = season_data.compare(
        "WINTER", 2024, "SPRING", 2024,
        post=by_season(spring=offline), cache_path=tmp_path / "season-compare.json",
    )

    assert result.error_text == (
        "Could not load: AniList (Spring 2024): the request timed out"
    )
    assert [entry["title"] for entry in result.only_first] == ["Frieren", "Solo Leveling"]
    assert result.only_second == [] and result.shared == []


def test_entry_lines_and_details_are_readable():
    entry = {
        "key": "anilist:1", "title": "Frieren", "score": 90, "episodes": 28,
        "format": "TV", "status": "FINISHED", "season": "winter", "year": 2024,
    }
    assert season_data.entry_line(entry) == "Frieren — score 90 · TV"
    details = season_data.entry_details(entry)
    assert "Score 90/100" in details
    assert "28 episodes" in details
    assert "Winter 2024" in details

    unknown = season_data.entry_details({"title": "Unknown", "score": None, "episodes": None})
    assert "Score unknown" in unknown and "Episode count unknown" in unknown
    assert season_data.entry_details(None) == ""


def test_season_labels_and_defaults():
    assert season_data.season_key("spring", 2024) == "2024 spring"
    assert season_data.season_label("fall", 2025) == "Fall 2025"
    assert season_data.season_label("MONSOON", 2024) == "an unknown season"

    july = calendar.timegm((2026, 7, 1, 12, 0, 0))
    assert season_data.latest_season(july) == ("SUMMER", 2026)
    january = calendar.timegm((2026, 1, 5, 12, 0, 0))
    assert season_data.latest_season(january) == ("WINTER", 2026)
    assert season_data.previous_season("WINTER", 2026) == ("FALL", 2025)
    assert season_data.previous_season("SUMMER", 2026) == ("SPRING", 2026)
