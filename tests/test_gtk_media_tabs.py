"""Browser-style media tabs in the GTK frontend.

A real ``MoviesWindow`` is built offscreen, but every network path is replaced:
details, trailers and the anime page are stubs, and the player is a stand-in
web view, so these tests only exercise the tab behaviour.
"""

from __future__ import annotations

import time

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui  # noqa: E402
from lgl.tmdb import MediaItem  # noqa: E402


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


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class StubTmdb:
    """The window builds one; nothing may reach TMDB during these tests."""

    configured = True

    def __init__(self, *_a, **_k):
        pass

    def trending(self, *_a, **_k):
        return []

    def popular(self, *_a, **_k):
        return []

    def details(self, *_a, **_k):
        raise AssertionError("a tab restore must not refetch details")

    def season(self, *_a, **_k):
        raise AssertionError("a tab restore must not refetch a season")


@pytest.fixture
def window(display, monkeypatch):
    monkeypatch.setattr(movies_ui, "TmdbClient", StubTmdb)
    monkeypatch.setattr(movies_ui.MoviesWindow, "_load_home", lambda self: None)
    frame = Gtk.OffscreenWindow()
    media = movies_ui.MoviesWindow(
        frame, Store({"network_timeout": 5, "streaming_provider": "cinesrc"})
    )
    frame.add(media)
    frame.show_all()
    pump()
    # Tabs must never touch the network in these tests.
    media._load_details = lambda *_a, **_k: None
    media._load_trailer = lambda *_a, **_k: None
    media._load_anime_details = lambda *_a, **_k: None
    try:
        yield media
    finally:
        frame.destroy()
        pump(0.02)


def movie(tmdb_id: int = 1, title: str = "One", kind: str = "movie") -> MediaItem:
    return MediaItem(kind=kind, tmdb_id=tmdb_id, title=title)


def tab(window, key: str):
    return window._tabs.find(key)


def label_texts(widget) -> list[str]:
    found: list[str] = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_text())
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(label_texts(child))
    return found


def open_title(window, item) -> None:
    window._open_details(item)
    pump(0.02)


def test_opening_two_titles_creates_two_tabs(window):
    open_title(window, movie(1, "Interstellar"))
    open_title(window, movie(2, "Breaking Bad", "tv"))

    assert len(window._tabs) == 2
    assert [item.title for item in window._tabs] == ["Interstellar", "Breaking Bad"]
    assert window._tab_bar.get_visible() is True
    assert tab(window, "tv:2").subtitle == "TV Show"


def test_opening_an_open_title_selects_it_instead_of_duplicating(window):
    open_title(window, movie(1, "Interstellar"))
    open_title(window, movie(2, "Breaking Bad", "tv"))
    open_title(window, movie(1, "Interstellar"))

    assert len(window._tabs) == 2
    assert window._tabs.active.key == "movie:1"
    assert window._current_item.title == "Interstellar"


def test_switching_tabs_keeps_the_selected_episode(window):
    open_title(window, movie(1, "Interstellar"))
    open_title(window, movie(2, "Breaking Bad", "tv"))
    window._playing_episode = (2, 5)

    window._show_tab(window._tabs.tabs[0].tab_id)
    pump(0.02)
    assert window._current_item.title == "Interstellar"
    assert tab(window, "tv:2").season == 2
    assert tab(window, "tv:2").episode == 5
    assert tab(window, "tv:2").subtitle == "S2 · EP5"

    window._show_tab(window._tabs.tabs[1].tab_id)
    pump(0.02)
    assert window._current_item.title == "Breaking Bad"


def test_switching_pauses_the_player_and_saves_its_position(window):
    open_title(window, movie(1, "Interstellar"))
    open_title(window, movie(2, "Breaking Bad", "tv"))
    window._stack.set_visible_child_name("player")
    window._webview = object()
    window._playback_url = "https://provider.test/watch/2"
    window._playback_provider = "cinesrc"
    window._playback_title = "Breaking Bad"
    window._playback_position = 754.0
    # A real GLib source, so stopping it is the path the window really uses.
    window._position_source = GLib.timeout_add(10_000, lambda: False)

    window._show_tab(window._tabs.tabs[0].tab_id)
    pump(0.02)

    saved = tab(window, "tv:2")
    assert saved.playing is True
    assert saved.url == "https://provider.test/watch/2"
    assert saved.provider == "cinesrc"
    assert saved.position == pytest.approx(754.0)
    # The web view and its sampler are released with the tab.
    assert window._webview is None
    assert window._position_source == 0


def test_returning_to_a_playing_tab_restores_it_paused(window):
    open_title(window, movie(1, "Interstellar"))
    open_title(window, movie(2, "Breaking Bad", "tv"))
    window._stack.set_visible_child_name("player")
    window._webview = object()
    window._playback_url = "https://provider.test/watch/2"
    window._playback_title = "Breaking Bad"
    window._playback_position = 754.0
    window._show_tab(window._tabs.tabs[0].tab_id)
    pump(0.02)

    window._show_tab(window._tabs.tabs[1].tab_id)
    pump(0.02)

    assert window._stack.get_visible_child_name() == "player"
    texts = label_texts(window._player)
    assert any("12:34" in text for text in texts)
    # Nothing started playing by itself: there is no web view until Resume.
    assert window._webview is None


def test_resume_asks_for_the_saved_position(window):
    open_title(window, movie(1, "Interstellar"))
    tab_state = tab(window, "movie:1")
    window._tabs.update(
        tab_state.tab_id, playing=True, url="https://provider.test/1",
        title="Interstellar", position=754.0, provider="cinesrc",
    )
    calls: list[dict] = []
    window._open_player = lambda url, title, **kwargs: calls.append(
        {"url": url, "title": title, **kwargs}
    )

    window._resume_tab(tab(window, "movie:1"))

    assert calls and calls[0]["url"] == "https://provider.test/1"
    assert calls[0]["start"] == pytest.approx(754.0)


def test_closing_the_active_tab_selects_a_neighbour(window):
    open_title(window, movie(1, "One"))
    open_title(window, movie(2, "Two"))
    open_title(window, movie(3, "Three"))
    middle = window._tabs.tabs[1]
    window._show_tab(middle.tab_id)
    pump(0.02)

    window._close_tab(middle.tab_id)
    pump(0.02)

    assert len(window._tabs) == 2
    assert window._tabs.active.key == "movie:3"
    assert window._current_item.title == "Three"


def test_closing_an_inactive_tab_keeps_the_active_one(window):
    open_title(window, movie(1, "One"))
    open_title(window, movie(2, "Two"))
    active_before = window._tabs.active_id
    page_before = window._stack.get_visible_child_name()

    window._close_tab(window._tabs.tabs[0].tab_id)
    pump(0.02)

    assert len(window._tabs) == 1
    assert window._tabs.active_id == active_before
    assert window._stack.get_visible_child_name() == page_before
    assert window._current_item.title == "Two"


def test_closing_the_last_tab_returns_to_the_media_home(window):
    open_title(window, movie(1, "One"))
    window._close_tab(window._tabs.active_id)
    pump(0.02)

    assert len(window._tabs) == 0
    assert window._stack.get_visible_child_name() == "home"
    assert window._tab_bar.get_visible() is False
    assert window._current_item is None


def test_tabs_stay_visible_when_navigating_the_media_pages(window):
    open_title(window, movie(1, "One"))
    window._activate_nav("home")
    pump(0.02)

    assert len(window._tabs) == 1
    assert window._tab_bar.get_visible() is True
    assert window._stack.get_visible_child_name() == "home"


def test_reopening_an_open_tv_tab_restores_its_session(window):
    open_title(window, movie(2, "Breaking Bad", "tv"))
    open_title(window, movie(1, "One"))
    show_tab = tab(window, "tv:2")
    window._tabs.update(
        show_tab.tab_id, season=2, episode=5, scroll=140, playing=True,
        url="https://provider.test/2", position=754.0, provider="cinesrc",
    )
    restores: list = []
    original = window._open_details

    def spy(item, *, restore=None):
        restores.append(restore)
        return original(item, restore=restore)

    window._open_details = spy

    open_title(window, movie(2, "Breaking Bad", "tv"))

    assert restores and restores[-1] is not None
    reopened = tab(window, "tv:2")
    assert reopened.season == 2 and reopened.episode == 5
    assert reopened.url == "https://provider.test/2"
    assert reopened.position == pytest.approx(754.0)
    assert reopened.playing is True
    assert window._playing_episode == (2, 5)
    # The saved position comes back paused, with nothing autoplaying.
    assert window._stack.get_visible_child_name() == "player"
    assert window._webview is None
    assert any("12:34" in text for text in label_texts(window._player))


def test_reopening_an_open_anime_tab_keeps_its_episode_list(window):
    from lgl import anipm

    title = anipm.AniTitle(anilist_id=7, title="Frieren")
    series = anipm.AniSeries(anilist_id=7, title="Frieren")
    episode = anipm.AniEpisode(number=3, title="The Journey's End")
    window._open_anime_details(title)
    pump(0.02)
    window._current_ani_series = series
    window._current_ani_episodes = [episode]
    window._playing_episode = (1, 3)
    open_title(window, movie(1, "Other"))  # captures the anime tab's state
    loads: list = []
    window._load_anime_details = lambda *a, **k: loads.append(True)

    window._open_anime_details(title)
    pump(0.02)

    assert loads == []  # no refetch: the tab's own data came back
    assert window._current_ani_series is series
    assert window._current_ani_episodes == [episode]
    assert window._playing_episode == (1, 3)
    assert window._anime_playing_key == "anipm:7"


def test_a_paused_tab_keeps_its_place_across_switches(window):
    open_title(window, movie(1, "One"))
    open_title(window, movie(2, "Two"))
    window._show_tab(tab(window, "movie:1").tab_id)
    pump(0.02)
    # A tab that was playing has a stream saved; its page opens on the panel.
    window._tabs.update(
        tab(window, "movie:2").tab_id, season=2, episode=5, playing=True,
        url="https://provider.test/2", position=754.0, provider="cinesrc",
    )
    window._show_tab(tab(window, "movie:2").tab_id)
    pump(0.02)
    assert window._stack.get_visible_child_name() == "player"

    # Away without pressing Resume, then back.
    window._show_tab(tab(window, "movie:1").tab_id)
    pump(0.02)
    assert tab(window, "movie:2").url == "https://provider.test/2"
    assert tab(window, "movie:2").position == pytest.approx(754.0)
    assert tab(window, "movie:2").playing is True

    window._show_tab(tab(window, "movie:2").tab_id)
    pump(0.02)
    assert window._webview is None
    assert any("12:34" in text for text in label_texts(window._player))


def test_leaving_the_media_view_stops_playback_and_keeps_the_tab(window):
    open_title(window, movie(1, "One"))
    window._stack.set_visible_child_name("player")
    window._webview = object()
    window._playback_url = "https://provider.test/1"
    window._playback_provider = "cinesrc"
    window._playback_position = 90.0
    window._playback_key = "movie:1"
    window._playing_episode = (1, 2)
    window._position_source = GLib.timeout_add(10_000, lambda: False)

    window.leave_view()
    pump(0.02)

    saved = tab(window, "movie:1")
    assert saved.playing is True
    assert saved.url == "https://provider.test/1"
    assert saved.position == pytest.approx(90.0)
    assert window._webview is None
    assert window._position_source == 0
    # The page left behind is the paused panel, never an empty player.
    assert window._stack.get_visible_child_name() == "player"
    assert any("1:30" in text for text in label_texts(window._player))


def test_hero_playback_opens_the_hero_titles_own_tab(window):
    open_title(window, movie(1, "Interstellar"))
    played: list = []
    window._play = lambda item: played.append(item)

    window._hero_watch(movie(2, "Dune"))
    pump(0.02)

    assert window._tabs.active.key == "movie:2"
    assert [item.tmdb_id for item in played] == [2]
    assert tab(window, "movie:1").playing is False


def test_playback_from_another_title_is_not_captured_into_the_active_tab(window):
    open_title(window, movie(1, "Interstellar"))
    window._playback_key = "movie:99"
    window._playback_url = "https://provider.test/99"
    window._playback_position = 300.0
    window._stack.set_visible_child_name("player")

    window._capture_active_tab()

    saved = tab(window, "movie:1")
    assert saved.playing is False
    assert saved.url == ""
    assert saved.position == 0.0


def buttons(widget) -> list:
    found: list = []
    if isinstance(widget, Gtk.Button):
        found.append(widget)
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            found.extend(buttons(child))
    return found


def test_next_episode_survives_a_tab_round_trip(window):
    from types import SimpleNamespace

    open_title(window, movie(2, "Breaking Bad", "tv"))
    open_title(window, movie(1, "One"))
    window._tabs.update(
        tab(window, "tv:2").tab_id, season=2, episode=5, playing=True,
        url="https://provider.test/2", position=10.0, provider="cinesrc",
    )
    clicked: list = []
    window._play_episode = lambda season, episode: clicked.append(
        (season, episode.number)
    )
    window._next_episode = lambda: (2, SimpleNamespace(number=6))

    window._show_tab(tab(window, "tv:2").tab_id)
    pump(0.02)

    labels = [button.get_label() for button in buttons(window._player)]
    assert "Next episode" in labels
    next_button = [
        button for button in buttons(window._player)
        if button.get_label() == "Next episode"
    ][0]
    next_button.clicked()
    assert clicked == [(2, 6)]
