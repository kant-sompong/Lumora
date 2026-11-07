"""The GTK hidden gems page on a display; skips itself without GTK 3 or a display."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_hidden  # noqa: E402
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


def page(tmp_path, sender=post, excluded=()):
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    return gtk_hidden.HiddenGemsPage(
        None, post=sender, cache_path=tmp_path / "hidden-gems.json",
        threaded=False, excluded=excluded,
    )


def labels(widget):
    return [row.get_child().get_text() for row in widget.get_children()]


def test_refresh_lists_gems_with_score_and_popularity_rank(tmp_path):
    view = page(tmp_path)
    view.refresh()
    assert labels(view.list) == [
        "Quiet Gem — score 88 · popularity rank 2 of 6",
        "Second Gem — score 82 · popularity rank 3 of 6",
        "Edge Gem — score 78 · popularity rank 4 of 6",
    ]
    assert view.status.get_text() == "3 of 6 titles score 78+ with popularity under 350."
    assert view.error.get_visible() is False
    assert int(view.popularity.get_value()) == 0
    assert "median of the fetched titles" in view.cutoff_note.get_text()


def test_selecting_a_gem_shows_its_numbers(tmp_path):
    view = page(tmp_path)
    view.refresh()
    view.list.select_row(view.list.get_row_at_index(0))
    assert "Score 88/100" in view.detail.get_text()
    assert "popularity 100 (rank 2 of 6)" in view.detail.get_text()


def test_a_network_failure_is_a_line_not_a_blank_page(tmp_path):
    def offline(_body):
        raise TimeoutError("timed out")

    view = page(tmp_path, sender=offline)
    view.refresh()
    assert view.error.get_visible() is True
    assert view.error.get_text() == "Could not load: AniList: the request timed out"
    assert labels(view.list) == []
    # the page still explains itself
    assert view.detail.get_text() == gtk_hidden.HINT


def test_a_failed_load_is_retried_when_the_tab_is_opened_again(tmp_path):
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, body):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("timed out")
            return post(body)

    view = page(tmp_path, sender=Flaky())
    view.ensure_loaded()
    assert view.loaded is False
    assert view.error.get_visible() is True

    view.ensure_loaded()
    assert view.loaded is True
    assert labels(view.list)


def test_thresholds_are_saved_and_the_median_stays_out_of_the_control(tmp_path):
    settings = Settings(tmp_path / "config.json")
    view = gtk_hidden.HiddenGemsPage(
        settings, post=post, cache_path=tmp_path / "hidden-gems.json", threaded=False
    )
    view.score.set_value(85)
    view.refresh()

    assert settings.get("hidden_gems_min_score") == 85
    assert settings.get("hidden_gems_max_popularity") == 0
    # The median that was used (350) is described in the note only.
    assert int(view.popularity.get_value()) == 0

    again = gtk_hidden.HiddenGemsPage(
        settings, post=post, cache_path=tmp_path / "hidden-gems.json", threaded=False
    )
    assert int(again.score.get_value()) == 85
    assert int(again.popularity.get_value()) == 0


def test_raising_the_score_suggests_lowering_it(tmp_path):
    view = page(tmp_path)
    view.refresh()
    view.score.set_value(100)
    view.refresh()
    assert labels(view.list) == []
    assert "Try lowering the score." in view.status.get_text()
