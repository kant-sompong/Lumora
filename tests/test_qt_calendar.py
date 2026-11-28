import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from lgl import anime_calendar as cal
from lgl import episode_calendar
from lgl import qt_calendar
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


def search_result(query="alpha"):
    return episode_calendar.CalendarResult(
        query=query,
        groups=[
            episode_calendar.SeriesGroup(
                "Alpha",
                "AniList",
                (
                    episode_calendar.Airing("Alpha", 5, NOW + 100, True, "AniList"),
                    episode_calendar.Airing(
                        "Alpha", 6, NOW + 8 * 86400, False, "AniList", date_only=True
                    ),
                    episode_calendar.Airing("Alpha", 7, None, False, "AniList"),
                ),
            )
        ],
    )


def test_search_shows_grouped_airings(app, settings, monkeypatch):
    monkeypatch.setattr(qt_calendar, "run_async", lambda fn, done, error=None: done(fn()))
    d = CalendarDialog(
        settings, autoload=False, search_loader=lambda query: search_result(query)
    )
    d.set_result(result())

    d._query.setText("alpha")
    d.search()

    shown = " | ".join(texts(d))
    assert "Alpha · AniList" in shown
    assert "E5" in shown and "E6" in shown and "E7" in shown
    assert "Estimated" in shown
    assert "Air date unknown" in shown
    assert "E5" not in " | ".join(text for text in texts(d) if "Film" in text)


def test_a_blank_search_goes_back_to_the_feed(app, settings, monkeypatch):
    monkeypatch.setattr(qt_calendar, "run_async", lambda fn, done, error=None: done(fn()))
    d = CalendarDialog(
        settings, autoload=False, search_loader=lambda query: search_result(query)
    )
    d.set_result(result())
    d._query.setText("alpha")
    d.search()
    assert "Alpha · AniList" in " | ".join(texts(d))

    d._query.setText("")
    d.search()
    shown = " | ".join(texts(d))
    assert "Alpha · AniList" not in shown and "Film" in shown


def test_an_empty_search_says_so(app, settings, monkeypatch):
    monkeypatch.setattr(qt_calendar, "run_async", lambda fn, done, error=None: done(fn()))
    d = CalendarDialog(
        settings,
        autoload=False,
        search_loader=lambda query: episode_calendar.CalendarResult(query=query),
    )
    d.set_result(result())
    d._query.setText("nothing")
    d.search()
    assert "No upcoming episodes" in d._status.text()
