"""Global search: grouping, folding and locality."""

from __future__ import annotations

from types import SimpleNamespace

from lgl.search import fold, score, search


def game(identifier, title, creator=""):
    return SimpleNamespace(identifier=identifier, title=title, creator=creator)


def test_fold_ignores_case_and_accents():
    assert fold("Amélie") == "amelie"
    assert fold("  The   Long Dark ") == "the long dark"
    assert score("amelie", "amel") == 80
    assert score("the long dark", "long") == 60
    assert score("the long dark", "zzz") == 0


def test_results_are_grouped_by_type():
    results = search(
        "dark",
        entries=[game("long-dark", "The Long Dark")],
        media=[
            {"kind": "movie", "title": "Dark City", "year": "1998"},
            {"kind": "tv", "title": "Dark", "year": "2017"},
        ],
        collections=[SimpleNamespace(name="Dark and moody")],
    )
    assert [entry.identifier for entry in results.games] == ["long-dark"]
    assert [entry["title"] for entry in results.movies] == ["Dark City"]
    assert [entry["title"] for entry in results.tv] == ["Dark"]
    assert [collection.name for collection in results.collections] == ["Dark and moody"]
    assert results.total == 4 and results.empty is False


def test_empty_query_returns_nothing():
    results = search("", entries=[game("a", "Anything")])
    assert results.empty is True
    assert results.games == []


def test_ranking_puts_exact_titles_first():
    results = search(
        "dark",
        entries=[game("x", "Darker Than Black"), game("y", "Dark")],
    )
    assert [entry.identifier for entry in results.games] == ["y", "x"]


def test_limit_is_respected_and_zero_means_none():
    entries = [game(f"g{i}", f"Dark {i}") for i in range(10)]
    assert len(search("dark", entries=entries, limit=3).games) == 3
    assert search("dark", entries=entries, limit=0).empty is True


def test_creator_and_identifier_match():
    results = search("ferox", entries=[game("ferox_games", "Something", creator="Ferox")])
    assert len(results.games) == 1
