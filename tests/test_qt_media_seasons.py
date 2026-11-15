"""A reply for the show you just left must change nothing on the page.

Each test drives the real ``MediaPage`` with the async layer swapped for a queue,
so a slow answer can be delivered deliberately after the user has moved on.  No
Qt WebEngine and no network are involved.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lgl import qt_media  # noqa: E402

SHOWS = {
    100: {
        "title": "Better Call Saul",
        "seasons": [(1, 10), (2, 10), (3, 10)],
        "episodes": {1: ["Uno", "Mijo"], 2: ["Switch", "Cobbler"]},
    },
    200: {
        "title": "South Park",
        "seasons": [(1, 13), (26, 6)],
        "episodes": {1: ["Cartman Gets an Anal Probe"], 26: ["Cupid Ye"]},
    },
}


class Details:
    def __init__(self, tmdb_id: int) -> None:
        self.tmdb_id = tmdb_id
        self.imdb_id = ""
        self.rating = None
        self.vote_count = 0
        self.seasons = [
            SimpleNamespace(number=number, name=f"Season {number}",
                            episode_count=count, poster_url="")
            for number, count in SHOWS[tmdb_id]["seasons"]
        ]


class Season:
    def __init__(self, tmdb_id: int, number: int) -> None:
        self.tmdb_id = tmdb_id
        self.number = number
        self.episodes = [
            SimpleNamespace(number=index + 1, title=title, air_date="", still_url="",
                            rating=None, runtime=0, overview="")
            for index, title in enumerate(SHOWS[tmdb_id]["episodes"].get(number, []))
        ]


class FakeTmdb:
    """Only what the detail page asks for, answered for the right show."""

    configured = True

    def details(self, _kind: str, tmdb_id: int) -> Details:
        return Details(tmdb_id)

    def season(self, tmdb_id: int, number: int) -> Season:
        return Season(tmdb_id, number)

    def images(self, _kind: str, _tmdb_id: int, language: str | None = None) -> object:
        return SimpleNamespace(
            logo_url="", logo_aspect=0.0, logo_width=0, logo_height=0,
            backdrop_url="",
        )


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def page(app, monkeypatch, tmp_path):
    """A Media page whose async work waits in ``pending`` until a test delivers it."""
    pending: list[tuple] = []

    def record(work, done, fail=None):
        pending.append((work, done, fail))

    monkeypatch.setattr(qt_media, "run_async", record)
    monkeypatch.setattr(qt_media.recently_viewed, "record", lambda *a, **k: None)
    # Artwork and trailers only decorate the page; nothing here is about them.
    monkeypatch.setattr(qt_media.MediaPage, "_load_image", lambda *a, **k: None)
    monkeypatch.setattr(qt_media.MediaPage, "_load_trailer", lambda *a, **k: None)
    page = qt_media.MediaPage(settings=None, cache_root=tmp_path)
    # The page reloads itself on the next event-loop turn; do that now, while it
    # is still alive, so no queued reload outlives the test.
    page._loaded = True
    app.processEvents()
    page._tmdb = FakeTmdb()
    page.pending = pending
    return page


def card(tmdb_id: int) -> qt_media.Card:
    return qt_media.Card(
        key=f"tv:{tmdb_id}", title=SHOWS[tmdb_id]["title"], kind="tv", tmdb_id=tmdb_id,
    )


def deliver(page, kind, **match) -> bool:
    """Run the oldest waiting fetch whose result matches, then hand it over."""
    for index, (work, done, _fail) in enumerate(page.pending):
        result = work()
        if isinstance(result, kind) and all(
            getattr(result, name) == value for name, value in match.items()
        ):
            page.pending.pop(index)
            done(result)
            return True
    raise AssertionError(f"no pending {kind.__name__} for {match}")


def season_fetches(page) -> list[str]:
    """The season and detail answers still waiting, by show."""
    waiting = []
    for work, _done, _fail in page.pending:
        result = work()
        if isinstance(result, (Details, Season)):
            waiting.append(f"{type(result).__name__}:{result.tmdb_id}")
    return waiting


def episode_titles(page) -> list[str]:
    texts = [label.text() for label in page.episode_host.findChildren(QLabel)]
    return [text for text in texts if text]


def test_better_call_saul_then_south_park_keeps_south_park(page):
    page.open_card(card(100))
    page.open_card(card(200))

    deliver(page, Details, tmdb_id=200)
    deliver(page, Season, tmdb_id=200)
    assert page.season_combo.season_numbers() == [1, 26]
    assert "Cartman Gets an Anal Probe" in episode_titles(page)

    # Better Call Saul's answers arrive after the user has left it.
    deliver(page, Details, tmdb_id=100)

    assert page._detail_card.title == "South Park"
    assert page.season_combo.season_numbers() == [1, 26]
    assert page._seasons_card is page._detail_card
    assert page.episode_head.count.text() == "Season 1 · 1 episodes"
    assert "Cartman Gets an Anal Probe" in episode_titles(page)
    assert "Uno" not in episode_titles(page)
    assert season_fetches(page) == []          # nothing was asked for Better Call Saul


def test_south_park_then_better_call_saul_keeps_better_call_saul(page):
    page.open_card(card(200))
    page.open_card(card(100))

    deliver(page, Details, tmdb_id=100)
    deliver(page, Season, tmdb_id=100)
    assert page.season_combo.season_numbers() == [1, 2, 3]

    deliver(page, Details, tmdb_id=200)

    assert page._detail_card.title == "Better Call Saul"
    assert page.season_combo.season_numbers() == [1, 2, 3]
    assert page.episode_head.count.text() == "Season 1 · 2 episodes"
    assert "Uno" in episode_titles(page)
    assert "Cartman Gets an Anal Probe" not in episode_titles(page)
    assert season_fetches(page) == []


def test_a_stale_season_reply_cannot_replace_a_loaded_show(page):
    page.open_card(card(100))
    deliver(page, Details, tmdb_id=100)
    page.season_combo.set_current_number(2)
    page._season_changed()                      # BCS season 2 is on its way

    page.open_card(card(200))
    deliver(page, Details, tmdb_id=200)
    deliver(page, Season, tmdb_id=200)
    assert "Cartman Gets an Anal Probe" in episode_titles(page)

    deliver(page, Season, tmdb_id=100)          # the reply for the show left behind

    assert page._detail_card.title == "South Park"
    assert page.episode_head.count.text() == "Season 1 · 1 episodes"
    assert "Cartman Gets an Anal Probe" in episode_titles(page)
    assert "Switch" not in episode_titles(page)
    assert 2 not in page._season_cache          # nor cached for this page


def test_a_stale_detail_reply_arriving_after_a_newer_show_loaded_changes_nothing(page):
    page.open_card(card(100))
    page.open_card(card(200))
    deliver(page, Details, tmdb_id=200)
    deliver(page, Season, tmdb_id=200)
    before = (page.season_combo.season_numbers(), episode_titles(page))

    deliver(page, Details, tmdb_id=100)

    assert (page.season_combo.season_numbers(), episode_titles(page)) == before
    assert [season.number for season in page._detail_seasons] == [1, 26]
    assert season_fetches(page) == []


def test_rapid_switching_settles_on_the_last_show(page):
    page.open_card(card(100))
    page.open_card(card(200))
    page.open_card(card(100))

    deliver(page, Details, tmdb_id=200)         # two stale answers, out of order
    deliver(page, Details, tmdb_id=100)
    deliver(page, Details, tmdb_id=100)
    deliver(page, Season, tmdb_id=100)

    assert page._detail_card.title == "Better Call Saul"
    assert page.season_combo.season_numbers() == [1, 2, 3]
    assert page.episode_head.count.text() == "Season 1 · 2 episodes"
    assert "Uno" in episode_titles(page)
    assert "Cupid Ye" not in episode_titles(page)


def test_choosing_a_season_still_loads_that_season(page):
    page.open_card(card(100))
    deliver(page, Details, tmdb_id=100)
    deliver(page, Season, tmdb_id=100)
    assert page.episode_head.count.text() == "Season 1 · 2 episodes"
    assert "Uno" in episode_titles(page)

    page.season_combo.set_current_number(2)
    page._season_changed()
    deliver(page, Season, tmdb_id=100)

    assert page._detail_card.title == "Better Call Saul"
    assert page.episode_head.count.text() == "Season 2 · 2 episodes"
    assert "Switch" in episode_titles(page)
    assert "Uno" not in episode_titles(page)
    assert 2 in page._season_cache


def test_a_season_picked_before_the_seasons_arrive_is_ignored(page):
    page.open_card(card(100))
    deliver(page, Details, tmdb_id=100)
    deliver(page, Season, tmdb_id=100)
    page.open_card(card(200))                   # South Park is still loading
    assert page.season_combo.season_numbers() == [1, 2, 3]  # still Better Call Saul's

    page._season_changed()                      # the user clicks it anyway

    # South Park's own details are still on the way, but no season was fetched
    # for it, and nothing from the show left behind was cached.
    assert [item for item in season_fetches(page) if item.startswith("Season")] == []
    assert page._season_cache == {}
