"""The Qt hidden gems page, offscreen: thresholds, list, failure line and note."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_hidden  # noqa: E402
from lgl.settings import Settings  # noqa: E402

CATALOGUE = [
    {"id": 1, "title": {"english": "Mega Hit"}, "averageScore": 90, "popularity": 100000,
     "episodes": 12, "format": "TV", "status": "FINISHED"},
    {"id": 2, "title": {"english": "Quiet Gem"}, "averageScore": 88, "popularity": 100,
     "episodes": 12, "format": "TV", "status": "FINISHED"},
    {"id": 3, "title": {"english": "Second Gem"}, "averageScore": 82, "popularity": 300,
     "episodes": 24, "format": "TV", "status": "FINISHED"},
    {"id": 4, "title": {"english": "Edge Gem"}, "averageScore": 78, "popularity": 200,
     "episodes": 12, "format": "TV", "status": "FINISHED"},
    {"id": 5, "title": {"english": "Below Score"}, "averageScore": 77, "popularity": 500,
     "episodes": 12, "format": "TV", "status": "FINISHED"},
    {"id": 6, "title": {"english": "Edge Popularity"}, "averageScore": 95, "popularity": 400,
     "episodes": 12, "format": "TV", "status": "FINISHED"},
]


def post(_body):
    return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": CATALOGUE}}}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def panel(tmp_path, *, sender=post, excluded=()):
    return qt_hidden.HiddenGemsPanel(
        None, post=sender, cache_path=tmp_path / "hidden-gems.json",
        threaded=False, excluded=excluded,
    )


def lines(widget):
    return [widget.item(row).text() for row in range(widget.count())]


def test_refresh_lists_gems_with_score_and_popularity_rank(app, tmp_path):
    page = panel(tmp_path)
    page.refresh()

    assert lines(page.list) == [
        "Quiet Gem — score 88 · popularity rank 2 of 6",
        "Second Gem — score 82 · popularity rank 3 of 6",
        "Edge Gem — score 78 · popularity rank 4 of 6",
    ]
    assert page.status.text() == "3 of 6 titles score 78+ with popularity under 350."
    assert page.error.isHidden() is True
    assert page.popularity.value() == 0
    assert "median of the fetched titles" in page.cutoff_note.text()
    assert page.list.isHidden() is False


def test_selecting_a_gem_shows_its_numbers(app, tmp_path):
    page = panel(tmp_path)
    page.refresh()

    page.list.setCurrentRow(0)

    assert "Score 88/100" in page.detail.text()
    assert "popularity 100 (rank 2 of 6)" in page.detail.text()


def test_library_titles_are_left_out(app, tmp_path):
    page = panel(tmp_path, excluded=["Quiet Gem", "anilist:3"])
    page.refresh()

    assert [line.split(" — ")[0] for line in lines(page.list)] == ["Edge Gem"]


def test_raising_the_score_empties_the_list_with_a_suggestion(app, tmp_path):
    page = panel(tmp_path)
    page.refresh()
    page.score.setValue(100)
    page.refresh()

    assert lines(page.list) == []
    assert "Try lowering the score." in page.status.text()
    assert page.error.isHidden() is True


def test_a_network_failure_is_a_line_not_a_blank_page(app, tmp_path):
    def offline(_body):
        raise TimeoutError("timed out")

    page = panel(tmp_path, sender=offline)
    page.refresh()

    assert page.error.isHidden() is False
    assert page.error.text() == "Could not load: AniList: the request timed out"
    assert lines(page.list) == []
    # The controls and the explanation remain; the empty list does not.
    assert page.score.value() == 78
    assert page.list.count() == 0
    assert page.list.isHidden() is True
    assert page.detail.isHidden() is True


def test_a_failed_load_is_retried_when_the_tab_is_opened_again(app, tmp_path):
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, body):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("timed out")
            return post(body)

    page = panel(tmp_path, sender=Flaky())
    page.ensure_loaded()
    assert page.loaded is False
    assert page.error.isHidden() is False

    page.ensure_loaded()
    assert page.loaded is True
    assert lines(page.list)
    assert page.list.isHidden() is False


def test_thresholds_are_saved_and_the_median_stays_out_of_the_control(app, tmp_path):
    settings = Settings(tmp_path / "config.json")
    page = qt_hidden.HiddenGemsPanel(
        settings, post=post, cache_path=tmp_path / "hidden-gems.json", threaded=False
    )
    page.score.setValue(85)
    page.refresh()

    assert settings.get("hidden_gems_min_score") == 85
    assert settings.get("hidden_gems_max_popularity") == 0
    # The median that was used (350) is described in the note only.
    assert page.popularity.value() == 0

    again = qt_hidden.HiddenGemsPanel(
        settings, post=post, cache_path=tmp_path / "hidden-gems.json", threaded=False
    )
    assert again.score.value() == 85
    assert again.popularity.value() == 0


def test_a_failed_load_keeps_the_error_line_beside_the_controls(app, tmp_path):
    def offline(_body):
        raise TimeoutError("timed out")

    page = panel(tmp_path, sender=offline)
    page.resize(1000, 900)
    page.show()
    app.processEvents()
    page.refresh()
    app.processEvents()

    gap = page.error.geometry().top() - page.score.geometry().bottom()
    assert gap < 150, f"the error line is {gap} px below the controls"
    assert page.list.isHidden() is True


def test_a_successful_load_still_fills_the_page_with_the_list(app, tmp_path):
    page = panel(tmp_path)
    page.resize(1000, 900)
    page.show()
    app.processEvents()
    page.refresh()
    app.processEvents()

    assert page.list.isHidden() is False
    assert page.list.height() > 300
    assert page.detail.isHidden() is False


def test_one_refresh_writes_both_thresholds_in_one_save(app, tmp_path):
    class Counting(Settings):
        def __init__(self, path):
            super().__init__(path)
            self.writes = 0

        def save(self):
            self.writes += 1
            super().save()

    settings = Counting(tmp_path / "config.json")
    page = qt_hidden.HiddenGemsPanel(
        settings, post=post, cache_path=tmp_path / "hidden-gems.json", threaded=False
    )
    page.score.setValue(85)
    page.refresh()

    assert settings.writes == 1
    assert settings.get("hidden_gems_min_score") == 85
    assert settings.get("hidden_gems_max_popularity") == 0


def test_a_second_refresh_is_answered_by_the_cache(app, tmp_path):
    page = panel(tmp_path)
    page.refresh()

    def explode(_body):
        raise AssertionError("the cache should have answered this")

    cached = panel(tmp_path, sender=explode)
    cached.refresh()

    assert len(lines(cached.list)) == 3
    assert "Showing saved titles." in cached.status.text()
    assert cached.error.isHidden() is True
