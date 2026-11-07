"""The Qt season comparison window: three lists, the summary, the error line.

Runs offscreen with the stub HTTP function from ``test_season_data``'s shape,
so nothing here touches AniList.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_season  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def media(ident, english, score=80, episodes=12):
    return {
        "id": ident,
        "title": {"english": english, "romaji": english},
        "averageScore": score,
        "episodes": episodes,
        "format": "TV",
        "status": "FINISHED",
    }


def post(body):
    season = body["variables"]["season"]
    rows = (
        [media(1, "Frieren", 90, 28), media(2, "Solo Leveling", 82)]
        if season == "WINTER"
        else [media(2, "Solo Leveling", 82), media(3, "Kaiju No. 8", 78)]
    )
    return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": rows}}}


def dialog(tmp_path, app, *, sender=post):
    window = qt_season.SeasonCompareDialog(
        None, post=sender, cache_path=tmp_path / "season-compare.json", threaded=False
    )
    for combo, value in ((window.season_a, "WINTER"), (window.season_b, "SPRING")):
        combo.setCurrentIndex(combo.findData(value))
    for combo in (window.year_a, window.year_b):
        combo.setCurrentIndex(combo.findData(2024))
    return window


def test_compare_fills_the_three_lists_and_the_summary(app, tmp_path):
    window = dialog(tmp_path, app)

    window.compare()

    titles = {
        key: [widget.item(row).text() for row in range(widget.count())]
        for key, widget in window.lists.items()
    }
    assert titles["only_first"] == ["Frieren — score 90 · TV"]
    assert titles["only_second"] == ["Kaiju No. 8 — score 78 · TV"]
    assert titles["shared"] == ["Solo Leveling — score 82 · TV"]
    assert window.summary.text() == (
        "Winter 2024: 2 titles, Spring 2024: 2 titles, 1 in both"
    )
    assert window.error.isHidden() is True
    assert window.detail.text() == qt_season.HINT
    # A successful comparison puts the three lists back.
    assert not any(w.isHidden() for w in window.lists.values())


def test_selecting_a_title_shows_its_score_and_episodes(app, tmp_path):
    window = dialog(tmp_path, app)
    window.compare()

    shared = window.lists["shared"]
    shared.setCurrentRow(0)

    assert "Score 82/100" in window.detail.text()
    assert "12 episodes" in window.detail.text()


def test_a_network_failure_is_a_line_and_not_an_empty_window(app, tmp_path):
    def offline(_body):
        raise TimeoutError("timed out")

    window = dialog(tmp_path, app, sender=offline)
    window.compare()

    assert window.error.isHidden() is False
    assert window.error.text() == (
        "Could not load: AniList (Winter 2024): the request timed out; "
        "AniList (Spring 2024): the request timed out"
    )
    assert window.summary.text() == "Nothing to compare"
    assert window.status.text() == "Nothing to compare yet."
    # The pickers and the error line survive; the empty lists do not.
    assert window.season_a.count() == 4
    assert [widget.count() for widget in window.lists.values()] == [0, 0, 0]
    assert all(w.isHidden() for w in window.lists.values())
    assert all(h.isHidden() for h in window.section_heads.values())


def test_the_saved_titles_note_appears_when_the_cache_answered(app, tmp_path):
    window = dialog(tmp_path, app)
    window.compare()

    def offline(_body):
        raise AssertionError("the cache should have answered this")

    again = dialog(tmp_path, app, sender=offline)
    again.compare()

    # Both seasons were cached a moment ago, so this run never touched the
    # network and says it is showing saved titles.
    assert again.lists["shared"].count() == 1
    assert "Showing saved titles" in again.status.text()
    assert again.error.isHidden() is True
