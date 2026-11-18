"""The GTK Search page asks TMDB, and only TMDB, for catalogue rows.

ani.pm is a player here: its series, episode and embed reads stay in use after
MyAnimeList verification, but its ``search`` is never a catalogue source.  The
tests replace ``AniPmClient.search`` with a recorder that fails if it is
reached, then drive the real search worker and grid.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import anipm, movies_ui, tmdb  # noqa: E402


@pytest.fixture(scope="module")
def display():
    if not Gtk.init_check()[0]:
        pytest.skip("no display")
    yield


def pump(seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration()
        time.sleep(0.005)


def wait_for(predicate, seconds: float = 3.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        pump(0.02)
        if predicate():
            return True
    return predicate()


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class StubTmdb:
    """A TMDB client the window may build; nothing leaves the process."""

    configured = True

    def __init__(self, *_a, **_k):
        pass

    def trending(self, kind="movie", **_k):
        return []

    def popular(self, kind="movie", **_k):
        return []


class Results:
    def __init__(self, items):
        self.items = list(items)

    def search(self, _query, **_k):
        return list(self.items)


@pytest.fixture
def window(display, monkeypatch):
    monkeypatch.setattr(movies_ui, "TmdbClient", StubTmdb)
    monkeypatch.setattr(movies_ui.MoviesWindow, "_load_home", lambda self: None)
    frame = Gtk.OffscreenWindow()
    media = movies_ui.MoviesWindow(frame, Store({"network_timeout": 5}))
    frame.add(media)
    frame.show_all()
    pump()
    feed = media.feed
    for row in feed.rows.values():
        row.set_loader(None)
    feed.schedule_loader = None
    feed.column_loaders.clear()
    try:
        yield media
    finally:
        frame.destroy()
        pump(0.02)


@pytest.fixture
def anipm_search_calls(monkeypatch):
    """Any ani.pm catalogue search is a failure, recorded for the assertion."""
    calls: list[str] = []

    def refused(self, query, **_kwargs):
        calls.append(str(query))
        raise AssertionError("ani.pm must never be searched for catalogue rows")

    monkeypatch.setattr(anipm.AniPmClient, "search", refused)
    return calls


def search(media, text: str) -> int:
    """Run the real search worker for *text* and return its token."""
    media._search_token += 1
    token = media._search_token
    media._run_search(text, token)
    return token


def test_the_search_page_shows_tmdb_rows_and_never_queries_anipm(
    window, anipm_search_calls
):
    item = tmdb.MediaItem(kind="movie", tmdb_id=603, title="The Matrix")
    window._client = Results([item])

    search(window, "matrix")

    assert wait_for(lambda: window._search_flow.get_children())
    assert anipm_search_calls == [], "ani.pm was asked for catalogue rows"
    children = window._search_flow.get_children()
    assert len(children) == 1
    assert isinstance(children[0], movies_ui.PosterTile)
    status = window._search_status.get_text()
    assert "from TMDB" in status
    assert "ani.pm" not in status


def test_an_empty_search_result_still_never_queries_anipm(
    window, anipm_search_calls
):
    window._client = Results([])

    search(window, "nothing matches this")

    assert wait_for(lambda: "No titles matched" in window._search_status.get_text())
    assert anipm_search_calls == []
    assert window._search_flow.get_children() == []
    assert "ani.pm" not in window._search_status.get_text()
