"""The ratings row: three painted badges, or nothing at all.

The fetcher is injected, so no test reaches OMDb.  What is under test is the
contract's shape: 16 px icon, 6 px gap, 15 px semibold number, 14 px between
badges, the order, the tooltips, and that a missing score removes the badge
instead of printing an error.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lgl import qt_ratings, ratings as ratings_core  # noqa: E402
from lgl.qt_ratings import (  # noqa: E402
    BADGE_GAP,
    ICON_GAP,
    ICON_SIZE,
    NUMBER_SIZE,
    RatingsRow,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, predicate=None, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(0.01)
    return predicate() if predicate is not None else True


PAYLOAD = {
    "Response": "True",
    "imdbRating": "8.5",
    "Ratings": [{"Source": "Rotten Tomatoes", "Value": "90%"}],
}


def _row(app, fetcher, **kwargs):
    row = RatingsRow(
        None, theme="dark", fetcher=fetcher, cache_dir=kwargs.pop("cache_dir", None), **kwargs
    )
    row.show()
    app.processEvents()
    return row


def test_all_three_badges_appear_in_order(app, tmp_path):
    row = _row(
        app, lambda url: PAYLOAD, imdb_id="tt0133093", tmdb_vote=6.4, tmdb_votes=900,
        key="abc", cache_dir=tmp_path,
    )
    row.load()

    assert pump(app, lambda: len(row._badges) == 3)
    assert [badge.kind for badge in row._badges] == ["imdb", "rotten", "tmdb"]
    assert [badge._text for badge in row._badges] == ["8.5", "90%", "64%"]
    assert [badge.toolTip() for badge in row._badges] == [
        "IMDb", "Rotten Tomatoes (critics)", "TMDB user score",
    ]
    assert row.isVisible() is True
    for badge in row._badges:
        assert badge.width() > ICON_SIZE + ICON_GAP
    row.close()


def test_a_missing_score_hides_only_that_badge(app, tmp_path):
    row = _row(
        app, lambda url: {"Response": "True", "imdbRating": "7.1"},
        imdb_id="tt1", tmdb_vote=None, tmdb_votes=0, key="abc", cache_dir=tmp_path,
    )
    row.load()

    assert pump(app, lambda: len(row._badges) == 1)
    assert row._badges[0].kind == "imdb"
    row.close()


def test_no_data_hides_the_whole_row(app, tmp_path):
    row = _row(app, lambda url: {"Response": "False"}, imdb_id="", cache_dir=tmp_path)
    row.load()

    assert pump(app, lambda: row.isVisible() is False)
    assert row._badges == []
    row.close()


def test_a_failing_fetch_is_silent(app, tmp_path):
    def boom(url):
        raise TimeoutError("no answer")

    row = _row(
        app, boom, imdb_id="tt2", tmdb_vote=5.0, tmdb_votes=10, key="abc",
        cache_dir=tmp_path,
    )
    row.load()

    assert pump(app, lambda: len(row._badges) == 1)  # the TMDB score survives
    assert [badge.kind for badge in row._badges] == ["tmdb"]
    shown = " ".join(
        child.text() for child in row.findChildren(QLabel) if child.text()
    )
    assert "error" not in shown.casefold()
    row.close()


def test_a_cached_answer_does_not_call_the_fetcher_again(app, tmp_path):
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return PAYLOAD

    first = _row(app, fetch, imdb_id="tt3", key="abc", cache_dir=tmp_path)
    first.load()
    assert pump(app, lambda: len(first._badges) == 2)
    first.close()

    second = _row(app, fetch, imdb_id="tt3", key="abc", cache_dir=tmp_path)
    second.load()
    assert pump(app, lambda: len(second._badges) == 2)
    assert len(calls) == 1
    second.close()


def test_the_geometry_and_type_are_the_contract_numbers():
    assert ICON_SIZE == 16
    assert ICON_GAP == 6
    assert BADGE_GAP == 14
    assert NUMBER_SIZE == 15
    assert qt_ratings.IMDB_YELLOW == "#f5c518"


def test_the_badges_use_the_theme_tokens(app):
    row = _row(app, lambda url: PAYLOAD, imdb_id="tt4")
    row.show_ratings(ratings_core.Ratings(imdb=8.5, rotten=90, tmdb_percent=64))
    row.apply_theme("light")

    for badge in row._badges:
        assert "15px" in badge.number.styleSheet()
        assert "600" in badge.number.styleSheet()
    row.close()


def test_the_spacing_between_badges_is_14(app):
    row = _row(app, lambda url: PAYLOAD, imdb_id="tt5")
    row.show_ratings(ratings_core.Ratings(imdb=8.5, rotten=90, tmdb_percent=64))

    assert row.box.spacing() == 14
    assert all(badge.height() == 22 for badge in row._badges)
    row.close()
