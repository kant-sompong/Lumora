"""Hidden gems: thresholds, exclusions, caching and plain error messages.

``post`` is a stub everywhere, so the suite neither needs nor uses AniList.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from lgl import hidden_gems


def media(ident, title, score, popularity, *, episodes=12, fmt="TV", status="FINISHED"):
    return {
        "id": ident,
        "title": {"english": title, "romaji": title},
        "averageScore": score,
        "popularity": popularity,
        "coverImage": {"large": f"https://img.anilist.test/{ident}.jpg"},
        "episodes": episodes,
        "format": fmt,
        "status": status,
    }


#: Six titles so the median is a clean 350: (300 + 400) / 2.
CATALOGUE = [
    media(1, "Mega Hit", 90, 100000),
    media(2, "Quiet Gem", 88, 100),
    media(3, "Second Gem", 82, 300),
    media(4, "Edge Gem", 78, 200),
    media(5, "Below Score", 77, 500),
    media(6, "Edge Popularity", 95, 400),
]


def page(rows, *, has_next=False):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": has_next}, "media": rows}}}


def catalogue_post(*_args, **_kwargs):
    return page(CATALOGUE)


def entries():
    """The catalogue as the module stores it: score, popularity and rank."""
    return hidden_gems.fetch_catalogue(post=catalogue_post)


def test_the_query_asks_for_score_and_popularity_without_touching_the_season_query():
    assert "averageScore" in hidden_gems.QUERY
    assert "popularity" in hidden_gems.QUERY
    assert "sort: POPULARITY_DESC" in hidden_gems.QUERY

    from lgl import season_data

    assert "popularity" not in season_data.QUERY


def test_the_catalogue_keeps_anilist_order_as_the_rank():
    calls = []

    def post(body):
        calls.append(body)
        if body["variables"]["page"] == 1:
            return page([CATALOGUE[0], CATALOGUE[1]], has_next=True)
        return page([CATALOGUE[2]])

    entries = hidden_gems.fetch_catalogue(post=post)

    assert [entry["rank"] for entry in entries] == [1, 2, 3]
    assert [entry["key"] for entry in entries] == ["anilist:1", "anilist:2", "anilist:3"]
    assert entries[1]["score"] == 88 and entries[1]["popularity"] == 100
    assert calls[0]["variables"] == {"page": 1}
    assert calls[1]["variables"] == {"page": 2}


def test_rows_without_a_popularity_number_are_skipped():
    def post(_body):
        return page([
            {"id": 9, "title": {"english": "No popularity"}, "averageScore": 90},
            {"id": 10, "title": {"english": "Fine"}, "averageScore": 90, "popularity": 5},
            {"id": 0, "title": {"english": "No id"}, "popularity": 5},
            "junk",
        ])

    entries = hidden_gems.fetch_catalogue(post=post)
    assert [entry["title"] for entry in entries] == ["Fine"]


def test_the_median_is_the_middle_popularity():
    assert hidden_gems.median_popularity(entries()) == 350
    assert hidden_gems.median_popularity([]) == 0
    assert hidden_gems.median_popularity([{"popularity": 5}]) == 5


def test_score_and_popularity_edges_are_strict():
    gems, cutoff = hidden_gems.select(entries())

    assert cutoff == 350
    assert [entry["title"] for entry in gems] == ["Quiet Gem", "Second Gem", "Edge Gem"]
    # 78 passes, 77 does not; 200 passes, 350 (the cutoff itself) does not.
    titles = {entry["title"] for entry in gems}
    assert "Edge Gem" in titles and "Below Score" not in titles
    assert "Edge Popularity" not in titles


def test_explicit_thresholds_override_the_median():
    gems, cutoff = hidden_gems.select(entries(), min_score=85, max_popularity=150)

    assert cutoff == 150
    assert [entry["title"] for entry in gems] == ["Quiet Gem"]


def test_a_zero_popularity_means_the_median_again():
    gems, cutoff = hidden_gems.select(entries(), max_popularity=0)
    assert cutoff == 350
    assert len(gems) == 3


def test_a_title_exactly_on_an_explicit_cutoff_is_not_a_gem():
    gems, cutoff = hidden_gems.select(entries(), max_popularity=300)
    assert cutoff == 300
    assert [entry["title"] for entry in gems] == ["Quiet Gem", "Edge Gem"]


def test_library_and_followed_titles_are_left_out():
    gems, _cutoff = hidden_gems.select(
        entries(), excluded=["anilist:2", "Second Gem", "6"]
    )
    assert [entry["title"] for entry in gems] == ["Edge Gem"]


def test_is_excluded_matches_a_key_a_title_or_a_bare_id():
    entry = {"key": "anilist:42", "title": "Some Show"}
    assert hidden_gems.is_excluded(entry, {"anilist:42"})
    assert hidden_gems.is_excluded(entry, {"some show"})
    assert hidden_gems.is_excluded(entry, {"42"})
    assert not hidden_gems.is_excluded(entry, {"other"})


def test_gems_are_sorted_by_score_then_by_popularity():
    rows = [
        {"key": "anilist:1", "title": "B", "score": 80, "popularity": 50, "rank": 1},
        {"key": "anilist:2", "title": "A", "score": 90, "popularity": 900, "rank": 2},
        {"key": "anilist:3", "title": "C", "score": 80, "popularity": 10, "rank": 3},
    ]
    gems, _converted = hidden_gems.select(rows, max_popularity=1000)
    assert [entry["title"] for entry in gems] == ["A", "C", "B"]


def test_a_fresh_cache_answers_without_the_network(tmp_path):
    cache = tmp_path / "hidden-gems.json"
    first = hidden_gems.load_gems(post=catalogue_post, now=1000, cache_path=cache)
    assert first.from_cache is False
    assert len(first.entries) == 3

    def explode(_body):
        raise AssertionError("the cache should have answered this")

    second = hidden_gems.load_gems(post=explode, now=1000 + 60, cache_path=cache)
    assert second.from_cache is True
    assert [entry["title"] for entry in second.entries] == [
        "Quiet Gem", "Second Gem", "Edge Gem"
    ]


def test_the_cache_holds_titles_not_the_filtered_answer(tmp_path):
    cache = tmp_path / "hidden-gems.json"
    hidden_gems.load_gems(post=catalogue_post, now=1000, cache_path=cache)
    stored = json.loads(cache.read_text("utf-8"))
    assert len(stored["catalogue"]["entries"]) == len(CATALOGUE)

    stricter = hidden_gems.load_gems(
        now=1000 + 60, cache_path=cache, min_score=85, max_popularity=150
    )
    assert [entry["title"] for entry in stricter.entries] == ["Quiet Gem"]
    assert stricter.from_cache is True


def test_an_expired_cache_is_fetched_again(tmp_path):
    cache = tmp_path / "hidden-gems.json"
    hidden_gems.load_gems(post=catalogue_post, now=1000, cache_path=cache)

    def newer(_body):
        return page([media(7, "Brand New Gem", 91, 10)])

    fresh = hidden_gems.load_gems(
        post=newer, now=1000 + hidden_gems.CACHE_TTL + 1, cache_path=cache,
        max_popularity=1000,
    )
    assert fresh.from_cache is False
    assert [entry["title"] for entry in fresh.entries] == ["Brand New Gem"]


def test_a_failed_refresh_falls_back_to_saved_titles(tmp_path):
    cache = tmp_path / "hidden-gems.json"
    hidden_gems.load_gems(post=catalogue_post, now=1000, cache_path=cache)

    def offline(_body):
        raise urllib.error.URLError("Name or service not known")

    stale = hidden_gems.load_gems(
        post=offline, now=1000 + hidden_gems.CACHE_TTL + 1, cache_path=cache
    )
    assert stale.from_cache is True
    assert len(stale.entries) == 3
    assert "network error" in stale.error
    assert "Showing saved titles." in stale.note()


def test_a_timeout_is_one_readable_line(tmp_path):
    def slow(_body):
        raise TimeoutError("timed out")

    result = hidden_gems.load_gems(post=slow, cache_path=tmp_path / "h.json")
    assert result.entries == []
    assert result.error_text == "Could not load: AniList: the request timed out"


def test_an_anilist_error_message_is_kept(tmp_path):
    def broken(_body):
        return {"errors": [{"message": "Too many requests"}]}

    result = hidden_gems.load_gems(post=broken, cache_path=tmp_path / "h.json")
    assert result.error_text == "Could not load: AniList: Too many requests"


def test_an_empty_answer_is_an_error_not_a_silent_blank(tmp_path):
    def empty(_body):
        return page([])

    result = hidden_gems.load_gems(post=empty, cache_path=tmp_path / "h.json")
    assert result.error_text == "Could not load: AniList returned no anime."


def test_no_gem_says_what_to_try(tmp_path):
    result = hidden_gems.load_gems(
        post=catalogue_post, cache_path=tmp_path / "h.json", min_score=99
    )
    assert result.entries == []
    assert result.error_text == ""
    assert result.note() == (
        "Nothing scores 99+ below popularity 350 of the 6 titles fetched. "
        "Try lowering the score."
    )


def test_the_note_counts_what_passed(tmp_path):
    result = hidden_gems.load_gems(post=catalogue_post, cache_path=tmp_path / "h.json")
    assert result.note() == "3 of 6 titles score 78+ with popularity under 350."


def test_lines_and_details_show_score_and_rank():
    entry = {
        "key": "anilist:2", "title": "Quiet Gem", "score": 88, "popularity": 100,
        "rank": 2, "episodes": 12, "format": "TV", "status": "FINISHED",
    }
    assert hidden_gems.entry_line(entry, 6) == (
        "Quiet Gem — score 88 · popularity rank 2 of 6"
    )
    details = hidden_gems.entry_details(entry, 6)
    assert "Score 88/100" in details
    assert "popularity 100 (rank 2 of 6)" in details
    assert "12 episodes" in details
    assert hidden_gems.entry_details(None) == ""


def test_the_query_asks_for_cover_art_so_gems_can_show_posters():
    query = " ".join(hidden_gems.QUERY.split())
    assert "coverImage" in query
    assert "large" in query


def test_each_gem_keeps_the_poster_the_api_sent():
    entries = hidden_gems.fetch_catalogue(post=catalogue_post)
    assert entries
    assert all(entry["poster_url"].startswith("https://img.anilist.test/") for entry in entries)


def test_a_gem_without_cover_art_simply_has_no_poster():
    def post(_payload):
        return page([{"id": 5, "title": {"romaji": "Bare"}, "averageScore": 90,
                     "popularity": 10, "coverImage": None}])

    entries = hidden_gems.fetch_catalogue(post=post)
    assert entries[0]["poster_url"] == ""


def test_a_cache_from_before_cover_art_is_not_trusted(tmp_path):
    cache = tmp_path / "gems.json"
    cache.write_text(
        '{"version": 1, "catalogue": {"fetched_at": 1000, "entries": ['
        '{"key": "anilist:1", "title": "Old", "score": 90, "popularity": 1}]}}',
        "utf-8",
    )
    calls = []

    def post(payload):
        calls.append(payload)
        return catalogue_post(payload)

    result = hidden_gems.load_gems(post=post, cache_path=cache, now=1001)
    assert calls, "a v1 cache has no posters, so it must be fetched again"
    assert all(entry["poster_url"] for entry in result.entries)
