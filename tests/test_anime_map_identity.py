"""Is this TMDB title anime?  Only a kind-aware mapping record verifies it.

The records are the real values from Fribb/anime-lists for Attack on Titan
(tv 1429) and *A Silent Voice* (movie 378064), so the kind-aware the movie id
equals nothing on the television side case is a real one.  No test touches the
network: ``_fetch`` is stubbed or a cache file is written first.
"""

from __future__ import annotations

import json

import pytest

from lgl import anime_map

NOW = 1_000_000.0

ATTACK_ON_TITAN = [
    {"type": "TV", "anilist_id": 16498, "mal_id": 16498,
     "themoviedb_id": {"tv": 1429}, "season": {"tmdb": 1}},
    {"type": "TV", "anilist_id": 99147, "mal_id": 35760,
     "themoviedb_id": {"tv": 1429}, "season": {"tmdb": 3}},
]

SILENT_VOICE = {"type": "MOVIE", "anilist_id": 20954, "mal_id": 28851,
                "themoviedb_id": {"movie": [378064]}}

RECORDS = ATTACK_ON_TITAN + [SILENT_VOICE]


def write_cache(path, records, fetched_at=NOW) -> None:
    path.write_text(
        json.dumps({"version": 1, "fetched_at": fetched_at, "records": records}),
        "utf-8",
    )


@pytest.fixture()
def mapping(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, RECORDS)
    return anime_map.load_mapping(cache_path=path, now=NOW)


def test_attack_on_titan_is_verified(mapping):
    verdict = anime_map.is_anime("tv", 1429, mapping=mapping)
    assert verdict.is_anime is True
    assert verdict.verified is True
    assert verdict.mal_id == 16498
    assert verdict.anilist_ids == (16498, 99147)
    assert verdict.reason == "mapped by the anime list"


def test_a_silent_voice_is_a_verified_film(mapping):
    verdict = anime_map.is_anime("movie", 378064, mapping=mapping)
    assert verdict.is_anime and verdict.verified
    assert verdict.mal_id == 28851
    assert verdict.anilist_ids == (20954,)


def test_kind_is_checked_so_show_and_film_ids_do_not_mix(mapping):
    assert anime_map.is_anime("movie", 1429, mapping=mapping).is_anime is False
    assert anime_map.is_anime("tv", 378064, mapping=mapping).is_anime is False
    # ... and the reverse: a film record never verifies the same-numbered show.
    records = [{"type": "MOVIE", "anilist_id": 1, "mal_id": 2,
                "themoviedb_id": {"movie": [4242]}}]
    mixed = anime_map.MappingList(tuple(records))
    assert anime_map.is_anime("movie", 4242, mapping=mixed).verified is True
    assert anime_map.is_anime("tv", 4242, mapping=mixed).is_anime is False


def test_japanese_animation_with_no_mapping_is_not_verified(mapping):
    """A ja original language and the Animation genre prove nothing."""
    verdict = anime_map.is_anime(
        "tv", 987654, original_language="ja", genre_ids=[16, 35], mapping=mapping
    )
    assert verdict.is_anime is False
    assert verdict.verified is False
    assert verdict.mal_id is None
    assert verdict.anilist_ids == ()
    assert verdict.reason == "no verified anime match"


def test_western_animation_is_not_anime(mapping):
    verdict = anime_map.is_anime(
        "tv", 4567, original_language="en", genre_ids=[16], mapping=mapping
    )
    assert verdict.is_anime is False
    assert verdict.verified is False


def test_live_action_is_not_anime(mapping):
    verdict = anime_map.is_anime(
        "tv", 4567, original_language="ja", genre_ids=[18, 9648], mapping=mapping
    )
    assert verdict.is_anime is False


def test_no_list_and_no_network_verifies_nothing(tmp_path, monkeypatch):
    def offline():
        raise OSError("no network")

    monkeypatch.setattr(anime_map, "_fetch", offline)
    verdict = anime_map.is_anime(
        "tv",
        1234,
        original_language="ja",
        genre_ids=[16],
        cache_path=tmp_path / "missing.json",
    )
    assert verdict.is_anime is False
    assert verdict.verified is False
    assert verdict.reason == "no verified anime match"


def test_mapped_record_is_trusted_without_touching_the_network(cache_path, monkeypatch):
    def offline():
        raise OSError("no network")

    monkeypatch.setattr(anime_map, "_fetch", offline)
    verdict = anime_map.is_anime("tv", 1429, cache_path=cache_path)
    assert verdict.verified is True
    assert verdict.mal_id == 16498


@pytest.fixture()
def cache_path(tmp_path):
    path = tmp_path / "anime-list.json"
    write_cache(path, RECORDS)
    return path


def test_the_reverse_index_is_reused_for_the_same_loaded_list(mapping):
    anime_map._REVERSE_INDEX.clear()
    first = anime_map.is_anime("tv", 1429, mapping=mapping)
    built = dict(anime_map._REVERSE_INDEX)
    second = anime_map.is_anime("tv", 1429, mapping=mapping)
    assert first == second
    assert anime_map._REVERSE_INDEX == built
    assert len(anime_map._REVERSE_INDEX) == 1
