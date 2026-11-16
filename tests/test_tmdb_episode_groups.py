"""Re:Zero's production episode group: real numbers, no network.

The two fixtures in ``tests/fixtures`` were recorded from TMDB's public pages
on 2026-11-16 (there is no TMDB API key on this machine): the show's
``/tv/65942/episode_groups`` listing and the detail of group
``641eb9d6b234b9007ac67063`` ("Seasons (Production)").  Its 5 groups hold 170
episodes: a 85-episode specials group plus 4 production seasons of 25, 25, 16
and 19 episodes.  TMDB's standard layout for the same show is a 90-episode
season 0 and a single 85-episode season 1, so the group is exactly a
re-splitting of the same 85 regular episodes.

``episode_count`` is TMDB's total for the group (170, specials included) and
``regular_episode_count`` is the non-specials total (85), which is what
:func:`lgl.tmdb.choose_layout` compares against the show.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lgl import anime_map, tmdb

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GROUP_ID = "641eb9d6b234b9007ac67063"
SHOW_ID = 65942
KEY = "0123456789abcdef0123456789abcdef"


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


LISTING = read_fixture("tmdb_episode_groups_65942.json")
DETAIL = read_fixture("tmdb_episode_group_641eb9d6b234b9007ac67063.json")


def client_for(listing=None, detail=None, calls: list | None = None, fail=()):
    """A client answering the two recorded documents, recording each path.

    A path in *fail* fails once and then answers normally, so a retry can show
    that the failure was not cached.
    """
    remaining = set(fail)

    def fetch(path, params):
        if calls is not None:
            calls.append(path)
        if path in remaining:
            remaining.discard(path)
            raise tmdb.TmdbError("TMDB answered HTTP 500", kind="server", status=500)
        if path == f"/tv/{SHOW_ID}/episode_groups":
            return LISTING if listing is None else listing
        if path == f"/tv/episode_group/{GROUP_ID}":
            return DETAIL if detail is None else detail
        raise AssertionError(f"unexpected path {path}")

    return tmdb.TmdbClient(KEY, fetch=fetch)


def standard_seasons():
    """Re:Zero's standard TMDB layout: 90 specials, one 85-episode season."""
    return (
        tmdb.Season(number=0, name="Specials", episode_count=90),
        tmdb.Season(number=1, name="Season 1", episode_count=85),
    )


def test_episode_groups_reads_the_listing_and_the_regular_total():
    calls: list = []
    client = client_for(calls=calls)
    groups = client.episode_groups(SHOW_ID)
    assert calls == [f"/tv/{SHOW_ID}/episode_groups", f"/tv/episode_group/{GROUP_ID}"]
    assert len(groups) == 1
    info = groups[0]
    assert info.id == GROUP_ID
    assert info.name == "Seasons"
    assert info.type == 6  # production
    assert info.group_count == 5
    assert info.episode_count == 170  # TMDB's total, specials included
    assert info.regular_episode_count == 85  # 25 + 25 + 16 + 19
    assert info.network == "TV Tokyo"


def test_episode_groups_is_cached_per_show():
    calls: list = []
    client = client_for(calls=calls)
    first = client.episode_groups(SHOW_ID)
    second = client.episode_groups(SHOW_ID)
    assert first == second
    assert calls == [f"/tv/{SHOW_ID}/episode_groups", f"/tv/episode_group/{GROUP_ID}"]


def test_episode_group_keeps_original_numbers_and_order():
    group = client_for().episode_group(GROUP_ID)
    assert group.id == GROUP_ID
    assert group.name == "Seasons"
    assert group.type == 6
    assert [season.order for season in group.groups] == [0, 1, 2, 3, 4]
    assert [season.name for season in group.groups] == [
        "Specials", "Season 1", "Season 2", "Season 3", "Season 4",
    ]
    assert [len(season.episodes) for season in group.groups] == [85, 25, 25, 16, 19]
    second = group.groups[2]
    assert [episode.order for episode in second.episodes] == list(range(1, 26))
    # Every production season lives in TMDB season 1, numbered continuously.
    assert second.episodes[0].season_number == 1
    assert second.episodes[0].episode_number == 26
    assert second.episodes[-1].episode_number == 50
    assert second.episodes[0].name == "Each One's Promise"


def test_episode_group_is_cached_per_group():
    calls: list = []
    client = client_for(calls=calls)
    first = client.episode_group(GROUP_ID)
    second = client.episode_group(GROUP_ID)
    assert first == second
    assert calls == [f"/tv/episode_group/{GROUP_ID}"]


def test_failing_episode_groups_is_not_cached():
    calls: list = []
    client = client_for(calls=calls, fail=(f"/tv/{SHOW_ID}/episode_groups",))
    with pytest.raises(tmdb.TmdbError):
        client.episode_groups(SHOW_ID)
    groups = client.episode_groups(SHOW_ID)
    assert len(groups) == 1
    assert calls == [f"/tv/{SHOW_ID}/episode_groups", f"/tv/{SHOW_ID}/episode_groups",
                     f"/tv/episode_group/{GROUP_ID}"]


def test_failing_episode_group_is_not_cached():
    calls: list = []
    client = client_for(calls=calls, fail=(f"/tv/episode_group/{GROUP_ID}",))
    with pytest.raises(tmdb.TmdbError):
        client.episode_group(GROUP_ID)
    group = client.episode_group(GROUP_ID)
    assert len(group.groups) == 5
    assert calls == [f"/tv/episode_group/{GROUP_ID}", f"/tv/episode_group/{GROUP_ID}"]


def test_choose_layout_returns_the_production_group():
    groups = client_for().episode_groups(SHOW_ID)
    chosen = tmdb.choose_layout(standard_seasons(), groups)
    assert chosen is not None
    assert chosen.id == GROUP_ID


def test_group_as_seasons_gives_the_four_production_seasons():
    group = client_for().episode_group(GROUP_ID)
    seasons = tmdb.group_as_seasons(group)
    assert [season.number for season in seasons] == [1, 2, 3, 4]
    assert [season.name for season in seasons] == [
        "Season 1", "Season 2", "Season 3", "Season 4",
    ]
    assert [season.episode_count for season in seasons] == [25, 25, 16, 19]
    assert [len(season.episodes) for season in seasons] == [25, 25, 16, 19]
    first = seasons[0]
    assert [episode.number for episode in first.episodes] == list(range(1, 26))
    assert first.episodes[0].season == 1
    assert first.episodes[0].title == (
        "The End of the Beginning and the Beginning of the End"
    )


def test_group_episode_to_target_maps_back_to_tmdb():
    group = client_for().episode_group(GROUP_ID)
    assert tmdb.group_episode_to_target(group, 1, 1) == (1, 1)
    assert tmdb.group_episode_to_target(group, 1, 25) == (1, 25)
    assert tmdb.group_episode_to_target(group, 2, 1) == (1, 26)
    assert tmdb.group_episode_to_target(group, 4, 19) == (1, 85)


def test_group_episode_to_target_out_of_range_is_none():
    group = client_for().episode_group(GROUP_ID)
    assert tmdb.group_episode_to_target(group, 2, 26) is None
    assert tmdb.group_episode_to_target(group, 9, 1) is None
    assert tmdb.group_episode_to_target(group, 1, 0) is None
    assert tmdb.group_episode_to_target(group, 1, -3) is None


def test_group_target_resolves_through_the_anime_map(tmp_path):
    group = client_for().episode_group(GROUP_ID)
    target = tmdb.group_episode_to_target(group, 2, 1)
    assert target == (1, 26)
    records = [
        {"type": "TV", "anilist_id": 108465, "mal_id": 35760,
         "themoviedb_id": {"tv": SHOW_ID}, "season": {"tmdb": 1},
         "episode_offset": {"tmdb": 0}},
        {"type": "TV", "anilist_id": 206914, "mal_id": 39587,
         "themoviedb_id": {"tv": SHOW_ID}, "season": {"tmdb": 1},
         "episode_offset": {"tmdb": 25}},
    ]
    played = anime_map.tmdb_to_anilist(
        "tv", SHOW_ID, target[0], target[1],
        cache_path=tmp_path / "anime-list.json",
        fetch=lambda: records,
        now=1_000_000.0,
    )
    assert played is not None
    assert played.anilist_id == 206914
    assert played.episode == 1


def info(**changes) -> tmdb.EpisodeGroupInfo:
    base = dict(
        id=GROUP_ID, name="Seasons", description="", type=6, group_count=4,
        episode_count=85, network="TV Tokyo", regular_episode_count=85,
    )
    base.update(changes)
    return tmdb.EpisodeGroupInfo(**base)


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [info(group_count=1)],
        [info(episode_count=170, regular_episode_count=170)],
        [info(type=3)],
        [info(), info(id="another")],
    ],
    ids=["empty", "same-season-count", "different-episode-count", "dvd-order", "two-qualifiers"],
)
def test_choose_layout_refuses_anything_it_cannot_be_sure_about(groups):
    assert tmdb.choose_layout(standard_seasons(), groups) is None


def test_choose_layout_refuses_an_ordinary_show():
    seasons = (
        tmdb.Season(number=0, name="Specials", episode_count=4),
        tmdb.Season(number=1, name="Season 1", episode_count=10),
        tmdb.Season(number=2, name="Season 2", episode_count=10),
    )
    assert tmdb.choose_layout(seasons, []) is None
    assert tmdb.choose_layout(seasons, client_for().episode_groups(SHOW_ID)) is None


def test_choose_layout_needs_standard_seasons():
    assert tmdb.choose_layout((), [info()]) is None


# -- API-shaped payloads ----------------------------------------------------
#
# TMDB's documented example numbers both groups and episodes from 0, while the
# recorded Re:Zero pages number them from 1.  These payloads follow the API
# shape (with every field the parser reads) and cover both conventions; the
# episode's ``order`` is kept as given and only the position in the sorted list
# numbers it on the page.


def api_episode(order, season_number, episode_number, name, *, runtime=24):
    return {
        "air_date": f"2016-04-{episode_number:02d}",
        "episode_number": episode_number,
        "season_number": season_number,
        "name": name,
        "overview": f"{name} overview",
        "production_code": "",
        "still_path": f"/{name.replace(' ', '_')}.jpg",
        "vote_average": 8.1,
        "vote_count": 120,
        "order": order,
        "runtime": runtime,
    }


def api_group(name, order, episodes):
    return {"name": name, "order": order, "episodes": episodes}


def api_detail(*groups):
    return {"id": GROUP_ID, "name": "Seasons", "description": "", "type": 6,
            "groups": list(groups)}


def detail_client(payload):
    def fetch(path, params):
        if path == f"/tv/episode_group/{GROUP_ID}":
            return payload
        raise AssertionError(f"unexpected path {path}")

    return tmdb.TmdbClient(KEY, fetch=fetch)


def test_zero_based_orders_keep_the_first_episode_and_the_first_season():
    payload = api_detail(
        api_group("Season 1", 0, [
            api_episode(0, 1, 1, "One"),
            api_episode(1, 1, 2, "Two"),
        ]),
        api_group("Season 2", 1, [
            api_episode(0, 1, 3, "Three"),
            api_episode(1, 1, 4, "Four"),
        ]),
    )
    group = detail_client(payload).episode_group(GROUP_ID)
    assert [season.order for season in group.groups] == [0, 1]
    assert [episode.order for episode in group.groups[0].episodes] == [0, 1]
    seasons = tmdb.group_as_seasons(group)
    assert [season.number for season in seasons] == [0, 1]  # order 0 is a season
    assert [len(season.episodes) for season in seasons] == [2, 2]
    assert [episode.number for episode in seasons[0].episodes] == [1, 2]
    assert tmdb.group_episode_to_target(group, 0, 1) == (1, 1)
    assert tmdb.group_episode_to_target(group, 1, 1) == (1, 3)
    assert tmdb.group_episode_to_target(group, 1, 2) == (1, 4)


def test_one_based_groups_with_zero_based_episodes_and_a_named_specials_group():
    payload = api_detail(
        api_group("Specials", 0, [
            api_episode(0, 0, 1, "Special one"),
            api_episode(1, 0, 2, "Special two"),
        ]),
        api_group("Season 1", 1, [
            api_episode(0, 1, 1, "Pilot"),
            api_episode(1, 1, 2, "Second"),
        ]),
    )
    group = detail_client(payload).episode_group(GROUP_ID)
    seasons = tmdb.group_as_seasons(group)
    assert [season.number for season in seasons] == [1]
    assert [episode.number for episode in seasons[0].episodes] == [1, 2]
    assert tmdb.group_episode_to_target(group, 1, 1) == (1, 1)
    assert tmdb.group_episode_to_target(group, 1, 2) == (1, 2)


def test_a_group_of_original_season_zero_episodes_is_specials_without_the_name():
    payload = api_detail(
        api_group("Extras", 0, [
            api_episode(0, 0, 1, "Extra one"),
            api_episode(1, 0, 2, "Extra two"),
        ]),
        api_group("Season 1", 1, [
            api_episode(0, 1, 1, "Pilot"),
        ]),
    )
    group = detail_client(payload).episode_group(GROUP_ID)
    seasons = tmdb.group_as_seasons(group)
    assert [season.number for season in seasons] == [1]
    assert [season.name for season in seasons] == ["Season 1"]


def test_a_group_named_specials_is_specials_even_with_regular_episodes():
    payload = api_detail(
        api_group("Specials", 0, [api_episode(0, 1, 9, "Misplaced")]),
        api_group("Season 1", 1, [api_episode(0, 1, 1, "Pilot")]),
    )
    group = detail_client(payload).episode_group(GROUP_ID)
    assert [season.name for season in tmdb.group_as_seasons(group)] == ["Season 1"]


def test_episodes_are_sorted_by_order_not_by_payload_position():
    payload = api_detail(
        api_group("Season 1", 0, [
            api_episode(2, 1, 3, "Third"),
            api_episode(0, 1, 1, "First"),
            api_episode(1, 1, 2, "Second"),
        ]),
    )
    group = detail_client(payload).episode_group(GROUP_ID)
    assert [episode.name for episode in group.groups[0].episodes] == [
        "First", "Second", "Third",
    ]
    seasons = tmdb.group_as_seasons(group)
    assert [episode.title for episode in seasons[0].episodes] == [
        "First", "Second", "Third",
    ]
    assert tmdb.group_episode_to_target(group, 0, 1) == (1, 1)
    assert tmdb.group_episode_to_target(group, 0, 3) == (1, 3)


def test_regular_episode_count_ignores_specials_detected_by_their_episodes():
    detail = api_detail(
        api_group("Extras", 0, [
            api_episode(0, 0, 1, "Extra one"),
            api_episode(1, 0, 2, "Extra two"),
        ]),
        api_group("Season 1", 1, [api_episode(0, 1, 1, "Pilot")]),
        api_group("Season 2", 2, [api_episode(0, 1, 2, "Second")]),
    )

    def fetch(path, params):
        if path == f"/tv/{SHOW_ID}/episode_groups":
            return LISTING
        if path == f"/tv/episode_group/{GROUP_ID}":
            return detail
        raise AssertionError(f"unexpected path {path}")

    info_row = tmdb.TmdbClient(KEY, fetch=fetch).episode_groups(SHOW_ID)[0]
    assert info_row.regular_episode_count == 2  # the two specials do not count
