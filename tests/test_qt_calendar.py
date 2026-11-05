import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from lgl import anime_calendar as cal
from lgl import release_feed as feed
from lgl.qt_calendar import CalendarDialog
from lgl.settings import Settings

NOW = 1_800_000_000.0


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path):
    return Settings(tmp_path / "settings.json", keyring=lambda *a, **k: None)


def result():
    return feed.FeedResult(entries=[
        cal.Entry("anilist:1", "Alpha", "3", NOW + 100),
        cal.Entry("tmdb:movie:2", "Film", "", NOW + 90000, date_only=True),
    ], fetched_at=NOW)


def texts(dialog):
    return [w.text() for w in dialog.findChildren(QLabel)]


def test_dialog_groups_by_day_and_filters(app, settings):
    d = CalendarDialog(settings, autoload=False)
    d.set_result(result())
    shown = " | ".join(texts(d))
    assert "Alpha — 3" in shown and "Film" in shown
    d._filter.setCurrentIndex(d._filter.findData("movie"))
    shown = " | ".join(texts(d))
    assert "Film" in shown and "Alpha" not in shown


def test_follow_button_saves_and_removes(app, settings):
    d = CalendarDialog(settings, autoload=False)
    d.set_result(result())
    buttons = [b for b in d.findChildren(QPushButton) if b.text() == "Follow"]
    buttons[0].click()
    assert cal.follows(settings) == ["anilist:1"]
    d._render()
    again = [b for b in d.findChildren(QPushButton) if b.text() == "Following"]
    again[0].click()
    assert cal.follows(settings) == []


def test_offline_notes_are_shown(app, settings):
    d = CalendarDialog(settings, autoload=False)
    d.set_result(feed.FeedResult(entries=result().entries, errors=["Could not load anime (AniList): offline"], from_cache=True))
    assert "saved dates" in d._status.text() and "offline" in d._status.text()
