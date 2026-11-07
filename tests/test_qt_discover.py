"""Discover, favourites, activity and season comparison windows."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_discover  # noqa: E402

ITEMS = [
    {"key": "a", "title": "Alpha", "genres": ["RPG"], "rating": 9.0, "popularity": 10},
    {"key": "b", "title": "Beta", "genres": ["RPG"], "rating": 6.0, "popularity": 80},
    {"key": "c", "title": "Gamma", "genres": ["RPG", "Puzzle"], "rating": 8.0, "popularity": 5},
]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def titles(widget):
    return [widget.item(i).text().split("\n")[0] for i in range(widget.count())]


def test_recommendations_share_a_genre_and_skip_what_is_owned(app):
    dialog = qt_discover.DiscoverDialog(ITEMS, liked=["a"], owned=["a"])
    assert set(titles(dialog.recommendations)) == {"Beta", "Gamma"}


def test_hidden_gems_need_a_good_rating_and_low_popularity(app):
    dialog = qt_discover.DiscoverDialog(ITEMS)
    assert set(titles(dialog.gems)) == {"Alpha", "Gamma"}


def test_more_like_this_follows_the_seed(app):
    dialog = qt_discover.DiscoverDialog(ITEMS)
    assert dialog.seed.currentData() == "a"
    assert set(titles(dialog.similar_list)) == {"Beta", "Gamma"}

    dialog.seed.setCurrentIndex(dialog.seed.findData("c"))
    assert "Beta" in titles(dialog.similar_list)


def test_collection_gaps_prefer_the_better_rated_fill(app):
    dialog = qt_discover.DiscoverDialog(ITEMS, owned=["a"])
    assert titles(dialog.suggestions)[0] == "Gamma"


def test_favourites_keep_games_and_media_apart(app):
    dialog = qt_discover.FavouritesDialog(
        [
            {"key": "g", "title": "A game", "kind": "game"},
            {"key": "m", "title": "A movie", "kind": "movie"},
            {"key": "t", "title": "A series", "kind": "tv"},
        ]
    )
    assert titles(dialog.game_list) == ["A game"]
    assert set(titles(dialog.media_list)) == {"A movie", "A series"}


def test_activity_is_newest_first(app):
    dialog = qt_discover.ActivityTimelineDialog(
        [
            {"ts": 1700000000, "title": "Older"},
            {"ts": 1700100000, "title": "Newer"},
            {"ts": "not a date", "title": "Junk"},
        ]
    )
    assert dialog.list.item(0).text().endswith("Newer")
    assert dialog.list.count() == 2


def test_season_comparison_splits_shared_and_unique_titles(app):
    items = [
        {"key": "x", "title": "X", "season": "winter", "year": "2024"},
        {"key": "y", "title": "Y", "season": "winter", "year": "2024"},
        {"key": "z", "title": "Z", "season": "spring", "year": "2024"},
    ]
    dialog = qt_discover.SeasonCompareDialog(items)
    assert dialog.seasons == ["2024 spring", "2024 winter"]
    assert dialog.only_first.count() == 1
    assert dialog.only_second.count() == 2
    assert dialog.shared.count() == 0
    assert "Spring 2024" in dialog.summary.text()
