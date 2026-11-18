"""The Qt Search page asks TMDB and MyAnimeList, never ani.pm.

ani.pm is a player on this frontend too: its series and episode reads stay in
use for verified anime, but ``AniPmClient.search`` is not a catalogue source.
The test replaces that method with a recorder that fails if it is reached and
drives the real search worker.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import anipm, mal, qt_media, tmdb  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate, seconds: float = 8.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Store(dict):
    network_timeout = 5.0

    def get(self, key, default=None):
        return dict.get(self, key, default)


class Results:
    configured = True

    def __init__(self, items):
        self.items = list(items)

    def search(self, _query):
        return list(self.items)


class MalResults(Results):
    """A MAL client that remembers the titles it was asked for."""

    def __init__(self, items):
        super().__init__(items)
        self.queries: list[str] = []

    def search(self, query):
        self.queries.append(str(query))
        return super().search(query)


@pytest.fixture
def page(app):
    widget = qt_media.MediaPage(None)
    widget.settings = Store()
    widget._mal = None
    widget._load_posters_when_shown = lambda: None
    try:
        yield widget
    finally:
        widget.deleteLater()


@pytest.fixture
def anipm_search_calls(monkeypatch):
    """Any ani.pm catalogue search is a failure, recorded for the assertion."""
    calls: list[str] = []

    def refused(self, query, **_kwargs):
        calls.append(str(query))
        raise AssertionError("ani.pm must never be searched for catalogue rows")

    monkeypatch.setattr(anipm.AniPmClient, "search", refused)
    return calls


def search(page, text: str) -> None:
    """Type *text* without the debounce timer, then run the real worker."""
    page.search.blockSignals(True)
    page.search.setText(text)
    page.search.blockSignals(False)
    page._run_search()


def test_the_search_page_shows_tmdb_rows_and_never_queries_anipm(
    app, page, anipm_search_calls
):
    page._tmdb = Results([
        tmdb.MediaItem(kind="movie", tmdb_id=603, title="The Matrix"),
    ])

    search(page, "matrix")

    assert wait_for(app, lambda: page._search_grid.cards())
    assert anipm_search_calls == [], "ani.pm was asked for catalogue rows"
    titles = [poster.card.title for poster in page._search_grid.cards()]
    assert titles == ["The Matrix"]
    note = page.search_note.text()
    assert "1 result" in note
    assert "ani.pm" not in note


def test_the_search_page_still_asks_mal_and_shows_its_rows(
    app, page, anipm_search_calls
):
    """Removing ani.pm must not remove MAL, Qt's other catalogue source."""
    page._tmdb = Results([
        tmdb.MediaItem(kind="movie", tmdb_id=603, title="The Matrix"),
    ])
    mal_client = MalResults([
        mal.AnimeItem(
            id=16498,
            title="Attack on Titan",
            year="2013",
            media_type="tv",
            episodes=25,
        ),
    ])
    page._mal = mal_client

    search(page, "titan")

    assert wait_for(app, lambda: len(page._search_grid.cards()) == 2)
    assert mal_client.queries == ["titan"], "MAL was not asked for anime rows"
    assert anipm_search_calls == []
    titles = [poster.card.title for poster in page._search_grid.cards()]
    assert titles == ["The Matrix", "Attack on Titan"]
    note = page.search_note.text()
    assert "2 results" in note
    assert "ani.pm" not in note


def test_an_empty_search_result_still_never_queries_anipm(
    app, page, anipm_search_calls
):
    page._tmdb = Results([])

    search(page, "nothing matches this")

    assert wait_for(app, lambda: "No results" in page.search_note.text())
    assert anipm_search_calls == []
    assert page._search_grid.cards() == []
    assert "ani.pm" not in page.search_note.text()
