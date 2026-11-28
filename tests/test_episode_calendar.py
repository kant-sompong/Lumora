"""The searchable episode calendar: confirmed dates, honest estimates."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from lgl import episode_calendar as ec

NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC
DAY = 86400.0


def day(offset: float) -> str:
    base = datetime.fromtimestamp(NOW, tz=timezone.utc).date()
    return (base + timedelta(days=offset)).isoformat()


def anilist_payload(media):
    return {"data": {"Page": {"media": media}}}


def anilist_media(ident=1, title="Alpha", nodes=()):
    return {
        "id": ident,
        "title": {"romaji": title, "english": title},
        "airingSchedule": {"nodes": list(nodes)},
    }


def tv_get(search=(), detail=None, seasons=None):
    """A fake TMDB reader: search, one show, and its seasons."""
    def get(path, params=None):
        if path == "/search/tv":
            return {"results": list(search)}
        if path.startswith("/tv/") and path.count("/") == 2:
            return detail or {"seasons": [{"season_number": 1}]}
        if "/season/" in path:
            number = int(path.rsplit("/", 1)[-1])
            return (seasons or {}).get(number, {"episodes": []})
        raise AssertionError(f"unexpected TMDB path {path}")
    return get


def episodes(*rows):
    return {"episodes": [{"episode_number": number, "air_date": when} for number, when in rows]}


# -- AniList ---------------------------------------------------------------

def test_anilist_gives_confirmed_episodes_and_drops_the_past():
    payload = anilist_payload([anilist_media(nodes=[
        {"episode": 3, "airingAt": int(NOW - 30 * DAY)},
        {"episode": 4, "airingAt": int(NOW + 2 * DAY)},
    ])])
    result = ec.search("alpha", now=NOW, anilist=lambda body: payload, tmdb_get=None)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert (group.title, group.source) == ("Alpha", "AniList")
    assert [airing.episode for airing in group.airings] == [4]
    airing = group.airings[0]
    assert airing.confirmed and not airing.date_only
    assert "2027" in ec.format_date(airing, "UTC")
    assert ec.status_label(airing) == ec.CONFIRMED


def test_anilist_uses_the_english_title_and_skips_bad_rows():
    payload = anilist_payload([
        {"id": 0, "title": {"romaji": "No id"}},
        {"id": 2, "title": {}, "airingSchedule": {"nodes": []}},
        {"id": 3, "title": {"english": "Beta"}, "airingSchedule": {"nodes": [
            {"episode": 1, "airingAt": "not a time"},
            {"episode": 2, "airingAt": int(NOW + DAY)},
        ]}},
    ])
    result = ec.search("beta", now=NOW, anilist=lambda body: payload, tmdb_get=None)
    assert [group.title for group in result.groups] == ["Beta"]
    assert result.groups[0].airings[0].episode == 2


def test_a_failing_anilist_is_a_message_not_a_crash():
    def broken(body):
        raise OSError("offline")

    result = ec.search("alpha", now=NOW, anilist=broken, tmdb_get=None)
    assert result.groups == []
    joined = " ".join(result.errors)
    assert "AniList" in joined and "offline" in joined
    assert "TMDB key" in joined


# -- TMDB and estimation ---------------------------------------------------

def test_weekly_cadence_projects_the_next_listed_episodes():
    get = tv_get(
        search=[{"id": 10, "name": "Weekly Show"}],
        detail={"seasons": [{"season_number": 3}, {"season_number": 0}]},
        seasons={3: episodes(
            (1, day(0)), (2, day(7)), (3, day(14)),
            (4, None), (5, None), (6, None), (7, None),
        )},
    )
    result = ec.search("weekly", now=NOW, anilist=lambda body: anilist_payload([]), tmdb_get=get)
    group = result.groups[0]
    assert group.source == "TMDB"
    by_number = {airing.episode: airing for airing in group.airings}

    # Everything the provider dated is confirmed.
    for number in (1, 2, 3):
        assert by_number[number].confirmed
        assert by_number[number].date_only
    # Two dates six to seven days apart are a cadence: projects three, then stops.
    estimated = [by_number[number] for number in (4, 5, 6)]
    assert all(airing.estimated for airing in estimated)
    assert [ec.status_label(airing) for airing in estimated] == [ec.ESTIMATED] * 3
    gaps = [
        (by_number[number + 1].air_at - by_number[number].air_at) / DAY
        for number in (3, 4, 5)
    ]
    assert all(abs(gap - 7) < 0.01 for gap in gaps)
    # Episode 7 is beyond the projection cap, so it is listed as unknown.
    assert by_number[7].air_at is None
    assert ec.format_date(by_number[7]) == ec.UNKNOWN_DATE
    assert ec.status_label(by_number[7]) == ""


def test_one_confirmed_date_is_not_a_cadence():
    get = tv_get(
        search=[{"id": 10, "name": "Lonely Show"}],
        seasons={1: episodes((1, day(2)), (2, None), (3, None))},
    )
    result = ec.search("lonely", now=NOW, anilist=lambda body: anilist_payload([]), tmdb_get=get)
    airings = result.groups[0].airings
    assert [airing.episode for airing in airings if airing.air_at is None] == [2, 3]
    assert all(not airing.confirmed for airing in airings if airing.air_at is None)


def test_an_irregular_schedule_is_not_projected():
    get = tv_get(
        search=[{"id": 10, "name": "Patchy Show"}],
        seasons={1: episodes((1, day(-1)), (2, day(2)), (3, day(20)), (4, None))},
    )
    result = ec.search("patchy", now=NOW, anilist=lambda body: anilist_payload([]), tmdb_get=get)
    last = result.groups[0].airings[-1]
    assert last.episode == 4 and last.air_at is None


def test_at_most_two_undated_episodes_are_listed():
    get = tv_get(
        search=[{"id": 10, "name": "Quiet Show"}],
        seasons={1: episodes((1, day(1)), (2, None), (3, None), (4, None), (5, None))},
    )
    result = ec.search("quiet", now=NOW, anilist=lambda body: anilist_payload([]), tmdb_get=get)
    unknown = [airing for airing in result.groups[0].airings if airing.air_at is None]
    assert [airing.episode for airing in unknown] == [2, 3]


def test_estimate_ignores_confirmed_gaps_that_are_too_wide():
    anchors = [(1, NOW - 30 * DAY), (2, NOW - 14 * DAY)]
    assert ec.estimate(anchors, [3], now=NOW, horizon=NOW + 90 * DAY) == []
    projected = ec.estimate(
        [(1, NOW - 7 * DAY), (2, NOW)],
        [3, 4],
        now=NOW,
        horizon=NOW + 90 * DAY,
    )
    assert [number for number, _ in projected] == [3, 4]


def test_estimate_stops_at_the_horizon():
    anchors = [(1, NOW - 7 * DAY), (2, NOW)]
    assert ec.estimate(anchors, [3], now=NOW, horizon=NOW + 2 * DAY) == []


# -- merging, grouping and wording ----------------------------------------

def test_a_tmdb_duplicate_of_an_anime_is_dropped():
    get = tv_get(search=[{"id": 10, "name": "Alpha"}])
    anilist = anilist_payload([anilist_media(nodes=[{"episode": 1, "airingAt": int(NOW + DAY)}])])
    result = ec.search("alpha", now=NOW, anilist=lambda body: anilist, tmdb_get=get)

    assert len(result.groups) == 1
    assert result.groups[0].source == "AniList"


def test_merge_keeps_one_row_per_episode():
    airings = [
        ec.Airing("Show", 2, NOW + DAY, True, "TMDB", 1, True),
        ec.Airing("Show", 2, NOW + DAY, True, "TMDB", 1, True),
        ec.Airing("Show", 3, NOW + 8 * DAY, False, "TMDB", 1, True),
    ]
    merged = ec.merge([("Show", "TMDB", airings)])
    assert [airing.episode for airing in merged[0][2]] == [2, 3]


def test_groups_are_soonest_first_and_undated_last():
    groups = ec.group([
        ("Later", "TMDB", [ec.Airing("Later", 1, NOW + 9 * DAY, True, "TMDB", 1, True)]),
        ("Unknown", "TMDB", [ec.Airing("Unknown", 1, None, False, "TMDB", 1, True)]),
        ("Sooner", "AniList", [ec.Airing("Sooner", 1, NOW + DAY, True, "AniList")]),
    ])
    assert [group.title for group in groups] == ["Sooner", "Later", "Unknown"]


def test_labels_and_rows_read_naturally():
    assert ec.episode_label(2, 5) == "S2E5"
    assert ec.episode_label(None, 5) == "E5"
    assert ec.episode_label(2, None) == "S2"
    assert ec.episode_label(None, None) == ""

    airing = ec.Airing("Show", 5, NOW + DAY, False, "TMDB", 2, True)
    row = ec.row_text(airing, "UTC")
    assert row.startswith("S2E5 · ") and row.endswith(ec.ESTIMATED)


def test_empty_query_does_no_work():
    def never(body):
        raise AssertionError("no request expected")

    result = ec.search("   ", now=NOW, anilist=never, tmdb_get=None)
    assert result.groups == [] and result.errors == []
