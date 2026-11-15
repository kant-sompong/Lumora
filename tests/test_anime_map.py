"""TMDB episode -> AniList entry: season splits, films, ambiguity and caches.

The mapping records here are the real shapes (and, for Attack on Titan,
Frieren, 86 and *A Silent Voice*, the real values) from
Fribb/anime-lists' ``anime-list-full.json``.  Nothing touches the network: the
cache and the AniList search are both stubbed.
"""

from __future__ import annotations

import json

import pytest

from lgl import anime_map

NOW = 1_000_000.0

#: Attack on Titan on TMDB (tv 1429): seasons 1-4, with season 3 and season 4
#: each split into two AniList entries by an episode offset.
ATTACK_ON_TITAN = [
    {"type": "TV", "anilist_id": 16498, "mal_id": 16498,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 1, "tmdb": 1},
     "episode_offset": None},
    {"type": "OVA", "anilist_id": 18397, "mal_id": 18397,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 0, "tmdb": 0},
     "episode_offset": None},
    {"type": "TV", "anilist_id": 20958, "mal_id": 25777,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 2, "tmdb": 2}},
    {"type": "TV", "anilist_id": 99147, "mal_id": 35760,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 3, "tmdb": 3},
     "episode_offset": None},
    {"type": "TV", "anilist_id": 104578, "mal_id": 38524,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 3, "tmdb": 3},
     "episode_offset": {"tvdb": 12, "tmdb": 12}},
    {"type": "TV", "anilist_id": 110277, "mal_id": 40028,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 4, "tmdb": 4},
     "episode_offset": None},
    {"type": "TV", "anilist_id": 131681, "mal_id": 48583,
     "themoviedb_id": {"tv": 1429}, "season": {"tvdb": 4, "tmdb": 4},
     "episode_offset": {"tvdb": 16, "tmdb": 16}},
]

#: Frieren on TMDB (tv 209867): one TMDB season holding three AniList entries.
FRIEREN = [
    {"type": "TV", "anilist_id": 154587, "mal_id": 52991,
     "themoviedb_id": {"tv": 209867}, "season": {"tvdb": 1, "tmdb": 1}},
    {"type": "TV", "anilist_id": 182255, "mal_id": 59978,
     "themoviedb_id": {"tv": 209867}, "season": {"tvdb": 2, "tmdb": 1},
     "episode_offset": {"tmdb": 28}},
    {"type": "TV", "anilist_id": 209939, "mal_id": 63816,
     "themoviedb_id": {"tv": 209867}, "season": {"tvdb": 3, "tmdb": 1},
     "episode_offset": {"tmdb": 38}},
]

#: A film: the TMDB film id is a list in the real list.
SILENT_VOICE = {"type": "MOVIE", "anilist_id": 20954, "mal_id": 28851,
                "themoviedb_id": {"movie": [378064]}}

#: A standalone show: one entry, one TMDB season.
EIGHTY_SIX = {"type": "TV", "anilist_id": 116588, "mal_id": 41456,
              "themoviedb_id": {"tv": 100567},
              "season": {"tvdb": 1, "tmdb": 1}}

RECORDS = ATTACK_ON_TITAN + FRIEREN + [SILENT_VOICE, EIGHTY_SIX]


def write_cache(path, records, fetched_at=NOW) -> None:
    path.write_text(
        json.dumps({"version": 1, "fetched_at": fetched_at, "records": records}),
        "utf-8",
    )


@pytest.fixture()
def cache(tmp_path):
    """A fresh cache holding the fixture records, so no fetch is needed."""
    path = tmp_path / "anime-list.json"
    write_cache(path, RECORDS)
    return path


def lookup(cache_path, kind, tmdb_id, season=1, episode=1, **kwargs):
    return anime_map.tmdb_to_anilist(
        kind, tmdb_id, season, episode, cache_path=cache_path, now=NOW, **kwargs
    )


def never_search(_title):
    raise AssertionError("the mapping list had the answer; no search is needed")


# -- Attack on Titan: seasons 1-4, including the two split seasons ----------


def test_season_one_maps_to_its_single_entry(cache):
    found = lookup(cache, "tv", 1429, 1, 5, search=never_search)

    assert (found.anilist_id, found.episode, found.confident) == (16498, 5, True)


def test_season_two_maps_to_its_single_entry(cache):
    found = lookup(cache, "tv", 1429, 2, 3)

    assert (found.anilist_id, found.episode) == (20958, 3)


def test_season_three_part_one_keeps_the_episode_number(cache):
    found = lookup(cache, "tv", 1429, 3, 10)

    assert (found.anilist_id, found.episode) == (99147, 10)


def test_season_three_part_two_converts_the_episode_number(cache):
    # TMDB episode 13 is the first episode of AniList's "Part 2".
    found = lookup(cache, "tv", 1429, 3, 13)

    assert (found.anilist_id, found.episode, found.confident) == (104578, 1, True)


def test_the_last_episode_of_a_split_season_still_lands(cache):
    found = lookup(cache, "tv", 1429, 3, 22)

    assert (found.anilist_id, found.episode) == (104578, 10)


def test_season_four_part_two_converts_the_episode_number(cache):
    found = lookup(cache, "tv", 1429, 4, 20)

    assert (found.anilist_id, found.episode) == (131681, 4)


def test_a_season_the_list_does_not_know_is_none(cache):
    assert lookup(cache, "tv", 1429, 9, 1, search=never_search) is None


def test_an_ova_in_the_same_show_is_never_picked_for_a_real_season(cache):
    found = lookup(cache, "tv", 1429, 1, 1)

    assert found.anilist_id != 18397


def test_the_kind_is_case_insensitive(cache):
    assert lookup(cache, "TV", 1429, 1, 1).anilist_id == 16498


# -- one TMDB season, several entries --------------------------------------


def test_the_first_entry_covers_its_own_episodes(cache):
    found = lookup(cache, "tv", 209867, 1, 5)

    assert (found.anilist_id, found.episode) == (154587, 5)


def test_a_later_entry_takes_its_own_episode_number(cache):
    found = lookup(cache, "tv", 209867, 1, 30)

    assert (found.anilist_id, found.episode, found.confident) == (182255, 2, True)


def test_the_third_entry_of_one_season_is_reached(cache):
    found = lookup(cache, "tv", 209867, 1, 39)

    assert (found.anilist_id, found.episode) == (209939, 1)


# -- films and standalone shows --------------------------------------------


def test_a_film_maps_to_its_entry(cache):
    found = lookup(cache, "movie", 378064)

    assert (found.anilist_id, found.episode, found.confident) == (20954, 1, True)


def test_a_standalone_series_maps_whole(cache):
    found = lookup(cache, "tv", 100567, 1, 7)

    assert (found.anilist_id, found.episode) == (116588, 7)


# -- nothing known: search, then None --------------------------------------


def test_an_unknown_title_without_a_name_is_none(cache):
    assert lookup(cache, "tv", 999999999) is None


def test_an_unknown_title_falls_back_to_the_anilist_search(cache):
    found = lookup(cache, "tv", 999999999, 1, 6, title="Some Anime",
                   search=lambda title: 4242 if title == "Some Anime" else None)

    assert (found.anilist_id, found.episode, found.confident) == (4242, 6, True)
    assert "title" in found.reason


def test_a_failed_search_leaves_no_link(cache):
    assert lookup(cache, "tv", 999999999, title="Some Anime",
                  search=lambda _title: None) is None


def test_a_raising_search_is_not_an_exception(cache):
    def broken(_title):
        raise OSError("no network")

    assert lookup(cache, "tv", 999999999, title="Some Anime", search=broken) is None


def test_a_search_that_answers_a_non_id_is_no_link(cache):
    assert lookup(cache, "tv", 999999999, title="Some Anime",
                  search=lambda _title: "not-an-id") is None


def test_a_film_found_by_title_plays_episode_one(cache):
    found = lookup(cache, "movie", 999999999, 1, 9, title="A Film",
                   search=lambda _title: 77)

    assert (found.anilist_id, found.episode) == (77, 1)


# -- ambiguity: ask, never guess -------------------------------------------


def test_two_entries_at_the_same_start_are_not_guessed(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [
        {"type": "TV", "anilist_id": 1, "themoviedb_id": {"tv": 5},
         "season": {"tmdb": 1}},
        {"type": "TV", "anilist_id": 2, "themoviedb_id": {"tv": 5},
         "season": {"tmdb": 1}},
    ])

    found = lookup(path, "tv", 5, 1, 3, search=never_search)

    assert found.confident is False
    assert found.anilist_id == 0
    assert sorted(candidate.anilist_id for candidate in found.candidates) == [1, 2]
    assert "share" in found.reason


def test_an_episode_before_the_mapped_entry_is_not_guessed(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [
        {"type": "TV", "anilist_id": 7, "themoviedb_id": {"tv": 6},
         "season": {"tmdb": 2}, "episode_offset": {"tmdb": 12}},
    ])

    found = lookup(path, "tv", 6, 2, 3, search=never_search)

    assert found.confident is False
    assert [candidate.episode for candidate in found.candidates] == [1]


def test_a_film_with_two_entries_asks_too(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [
        {"type": "MOVIE", "anilist_id": 11, "themoviedb_id": {"movie": [8]}},
        {"type": "MOVIE", "anilist_id": 12, "themoviedb_id": {"movie": [8]}},
    ])

    found = lookup(path, "movie", 8)

    assert found.confident is False
    assert len(found.candidates) == 2


# -- the reverse lookup ----------------------------------------------------


def test_anilist_to_tmdb_reads_a_show(cache):
    assert anime_map.anilist_to_tmdb(116588, cache_path=cache, now=NOW) == (
        "tv", 100567, 1,
    )


def test_anilist_to_tmdb_reads_a_split_season(cache):
    assert anime_map.anilist_to_tmdb(104578, cache_path=cache, now=NOW) == (
        "tv", 1429, 3,
    )


def test_anilist_to_tmdb_reads_a_film(cache):
    assert anime_map.anilist_to_tmdb(20954, cache_path=cache, now=NOW) == (
        "movie", 378064, 0,
    )


def test_anilist_to_tmdb_prefers_the_show_when_both_are_linked(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [
        {"type": "TV", "anilist_id": 3, "themoviedb_id": {"tv": 9, "movie": [10]},
         "season": {"tmdb": 2}},
    ])

    assert anime_map.anilist_to_tmdb(3, cache_path=path, now=NOW) == ("tv", 9, 2)


def test_an_unknown_anilist_id_is_none(cache):
    assert anime_map.anilist_to_tmdb(999999999, cache_path=cache, now=NOW) is None


def test_an_invalid_anilist_id_is_none(cache):
    assert anime_map.anilist_to_tmdb("nope", cache_path=cache, now=NOW) is None


# -- the cache -------------------------------------------------------------


def test_a_fresh_cache_is_not_refreshed(cache):
    calls = []
    found = anime_map.load_mapping(
        cache_path=cache, now=NOW + 60, fetch=lambda: calls.append(1) or []
    )

    assert found.from_cache is True
    assert found.records and calls == []


def test_a_week_old_cache_is_refreshed(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [EIGHTY_SIX], fetched_at=NOW)
    fresh = [dict(EIGHTY_SIX, anilist_id=555)]

    found = anime_map.load_mapping(
        cache_path=path, now=NOW + anime_map.WEEK + 1, fetch=lambda: fresh
    )

    assert found.from_cache is False
    assert found.records[0]["anilist_id"] == 555
    assert json.loads(path.read_text("utf-8"))["records"][0]["anilist_id"] == 555


def test_a_cache_just_inside_the_week_is_kept(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [EIGHTY_SIX], fetched_at=NOW)

    found = anime_map.load_mapping(
        cache_path=path, now=NOW + anime_map.WEEK - 1,
        fetch=lambda: pytest.fail("a fresh cache must not be refetched"),
    )

    assert found.from_cache is True


def test_a_failed_refresh_keeps_the_stale_cache(cache):
    def offline():
        raise OSError("no network")

    found = anime_map.load_mapping(
        cache_path=cache, now=NOW + anime_map.WEEK + 1, fetch=offline
    )

    assert found.records
    assert found.from_cache is True
    assert "no network" in found.error


def test_no_cache_and_no_network_is_an_empty_list(tmp_path):
    found = anime_map.load_mapping(
        cache_path=tmp_path / "missing.json", now=NOW,
        fetch=lambda: (_ for _ in ()).throw(OSError("offline")),
    )

    assert found.records == ()
    assert found.error


def test_a_corrupt_cache_is_replaced_by_a_refresh(tmp_path):
    path = tmp_path / "anime-list.json"
    path.write_text("{not json", "utf-8")

    found = anime_map.load_mapping(
        cache_path=path, now=NOW, fetch=lambda: [EIGHTY_SIX]
    )

    assert found.from_cache is False
    assert anime_map.anilist_to_tmdb(116588, cache_path=path, now=NOW)


def test_a_corrupt_cache_falls_back_to_the_last_good_copy(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, RECORDS)
    # A refresh writes the new file next to the old one; simulate a crash
    # after the old file became the fallback copy and the new one was cut off.
    path.replace(path.with_name(path.name + ".bak"))
    path.write_text("{\"records\": [", "utf-8")

    found = anime_map.load_mapping(
        cache_path=path, now=NOW,
        fetch=lambda: pytest.fail("the fallback copy is fresh enough"),
    )

    assert found.from_cache is True
    assert anime_map.anilist_to_tmdb(16498, cache_path=path, now=NOW)[1] == 1429


def test_a_successful_refresh_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "anime-list.json"
    anime_map.load_mapping(cache_path=path, now=NOW, fetch=lambda: [EIGHTY_SIX])

    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_the_default_cache_lives_in_the_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        anime_map.platform_support, "cache_dir", lambda *a, **k: tmp_path
    )

    assert anime_map.default_cache_path() == tmp_path / "anime-list.json"


def test_lookups_work_without_any_cache_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(anime_map, "_fetch", lambda *a, **k: {"nope": []})

    assert anime_map.tmdb_to_anilist(
        "tv", 1429, 1, 1, cache_path=tmp_path / "none.json", now=NOW
    ) is None


def test_a_null_field_never_crashes_a_lookup(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, [
        {"type": "TV", "anilist_id": None, "themoviedb_id": None, "season": None},
        {"type": "TV", "anilist_id": 5, "themoviedb_id": {"tv": 12},
         "season": "nonsense"},
    ])

    assert anime_map.anilist_to_tmdb(5, cache_path=path, now=NOW) == ("tv", 12, 1)
    assert anime_map.tmdb_to_anilist(
        "tv", 12, 1, 2, cache_path=path, now=NOW
    ) is None
