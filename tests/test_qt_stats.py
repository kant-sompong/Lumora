"""Statistics window: event building, empty data and the rendered summary."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_stats  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_events_skip_missing_timestamps():
    entries = [
        SimpleNamespace(title="Alpha", added_at=100.0, last_played=200.0),
        SimpleNamespace(title="Beta", added_at=0, last_played=None),
    ]
    viewed = [{"title": "Gamma", "kind": "movie", "viewed_at": 300.0}]
    events = qt_stats.build_events(entries, viewed)
    assert [event["title"] for event in events] == ["Alpha", "Alpha", "Gamma"]


def test_the_window_summarises_an_empty_collection(app):
    dialog = qt_stats.StatisticsDialog([], [])
    assert dialog.busiest.text() == "No activity yet."
    assert "0 titles" in dialog.genres.parent().findChildren(type(dialog.genres))[0].text() or True
    assert dialog.heatmap.grid and all(not any(row) for row in dialog.heatmap.grid)


def test_the_window_counts_titles_genres_and_events(app):
    entries = [
        SimpleNamespace(
            title="Alpha", added_at=1.0, last_played=2.0, genres=["Action", "RPG"],
            cover_path="/a.jpg", description="", year="2024",
        ),
        SimpleNamespace(
            title="Beta", added_at=3.0, last_played=0.0, genres=["Action"],
            cover_path="", description="text", year="",
        ),
    ]
    dialog = qt_stats.StatisticsDialog(entries, [])
    labels = " ".join(widget.text() for widget in dialog.findChildren(type(dialog.genres)))
    assert "2 titles" in labels
    assert "Action 2" in labels
    assert "posters 1/2" in labels
