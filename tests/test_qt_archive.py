"""Filtering and sorting for the Qt Archive view (no display needed)."""

import pytest

pytest.importorskip("PySide6")

from lgl.archive import GameSummary  # noqa: E402
from lgl.qt_archive import (  # noqa: E402
    bare_title,
    filter_games,
    genre_counts,
    license_group,
    sort_games,
    year_matches,
)


def game(identifier, title, year="", size=None, downloads=0, subjects=(), licenseurl="", creator=""):
    return GameSummary(
        identifier=identifier, title=title, year=year, downloads=downloads,
        item_size=size, creator=creator, subjects=list(subjects), licenseurl=licenseurl,
    )


GAMES = [
    game("a", "Alpha (v1.0) [Linux]", "1994", 300, 5, ["Puzzle", "Retro"], "https://creativecommons.org/licenses/by/4.0/"),
    game("b", "beta", "2008", None, 50, ["Action"], "https://creativecommons.org/publicdomain/mark/1.0/"),
    game("c", "Gamma", "", 100, 0, ["puzzle"], ""),
    game("d", "Delta", "2021", 900, 7, [], "https://example.com/eula"),
]


def ids(games):
    return [g.identifier for g in games]


def test_bare_title_strips_version_and_tags():
    assert bare_title("Alpha (v1.0) [Linux]") == "Alpha"
    assert bare_title("  Plain   Name ") == "Plain Name"


@pytest.mark.parametrize("url,expected", [
    ("", "none"),
    ("https://creativecommons.org/publicdomain/mark/1.0/", "publicdomain"),
    ("https://creativecommons.org/licenses/by/4.0/", "cc"),
    ("https://example.com/eula", "other"),
])
def test_license_group(url, expected):
    assert license_group(url) == expected


def test_year_buckets():
    assert year_matches("1994", "1990s")
    assert year_matches("1989", "pre1990")
    assert year_matches("2021", "2020s")
    assert not year_matches("", "2010s")  # unknown year never matches a bucket
    assert year_matches("", "all")


def test_filters_combine():
    assert ids(filter_games(GAMES, year="1990s")) == ["a"]
    assert ids(filter_games(GAMES, license_="cc")) == ["a"]
    assert ids(filter_games(GAMES, genre="puzzle")) == ["a", "c"]  # case-insensitive
    assert ids(filter_games(GAMES, year="1990s", genre="Action")) == []


def test_sort_title_is_case_insensitive_and_reversible():
    # Alpha, beta, Delta, Gamma once case is ignored
    assert ids(sort_games(GAMES, "title", False)) == ["a", "b", "d", "c"]
    assert ids(sort_games(GAMES, "title", True)) == ["c", "d", "b", "a"]


def test_missing_values_sort_last_in_both_directions():
    assert ids(sort_games(GAMES, "year", False)) == ["a", "b", "d", "c"]
    assert ids(sort_games(GAMES, "year", True)) == ["d", "b", "a", "c"]
    assert ids(sort_games(GAMES, "size", True))[-1] == "b"  # no size recorded
    assert ids(sort_games(GAMES, "size", False))[-1] == "b"


def test_unknown_sort_key_falls_back_to_title():
    assert ids(sort_games(GAMES, "nonsense", False)) == ids(sort_games(GAMES, "title", False))


def test_genre_counts_merge_case():
    counts = dict(genre_counts(GAMES))
    assert counts["Puzzle"] == 2
    assert counts["Action"] == 1
