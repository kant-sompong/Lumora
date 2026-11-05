"""The Recently viewed window: rows, empty state and the open signal."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import recently_viewed, qt_recent  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_rows_are_newest_first_and_carry_kind_and_key(app, tmp_path):
    path = tmp_path / "history.json"
    recently_viewed.record(path, kind="game", key="a", title="Alpha", at=1.0)
    recently_viewed.record(
        path, kind="movie", key="12", title="Beta", subtitle="2024", at=2.0
    )
    dialog = qt_recent.RecentlyViewedDialog(path)
    assert dialog.list.count() == 2
    assert dialog.list.item(0).text().startswith("Beta")
    seen = []
    dialog.open_requested.connect(lambda kind, key: seen.append((kind, key)))
    dialog._open_item(dialog.list.item(0))
    assert seen == [("movie", "12")]


def test_an_empty_history_shows_the_explanation(app, tmp_path):
    dialog = qt_recent.RecentlyViewedDialog(tmp_path / "missing.json")
    assert dialog.list.count() == 0
    assert dialog.list.isHidden()
    assert not dialog.empty.isHidden()


def test_clearing_empties_the_file_and_the_rows(app, tmp_path):
    path = tmp_path / "history.json"
    recently_viewed.record(path, kind="anime", key="7", title="Gamma")
    dialog = qt_recent.RecentlyViewedDialog(path)
    dialog._clear()
    assert recently_viewed.load(path) == []
    assert dialog.list.count() == 0
