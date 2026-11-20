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
    from gi.repository import Gdk, GLib, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import movies_ui, streaming  # noqa: E402
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


def verified_session(window, provider: str = "ani") -> dict:
    """A verified anime session as :meth:`_play_forced_ani` builds it."""
    return {
        "kind": "tmdb",
        "item": window._current_item,
        "season": 1,
        "episode": None,
        "route": object(),
        "provider": provider,
        "number": 5,
    }


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


def test_the_tab_strip_actually_takes_space_on_screen(window):
    """The open titles must be laid out, not merely marked visible.

    The strip is no-show-all, so the window's own show_all() never reaches it.
    Showing the bar alone leaves its scroller hidden and the bar allocates a
    single pixel, which is invisible however the ``visible`` flag reads.
    """
    open_title(window, movie(1, "Interstellar"))
    open_title(window, movie(2, "Breaking Bad", "tv"))
    pump(0.1)

    assert window._tab_bar.get_mapped() is True
    assert window._tab_scroller.get_mapped() is True
    assert window._tab_bar.get_allocated_height() >= 40
    assert all(child.get_mapped() for child in window._tab_row.get_children())


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


def test_closing_the_active_tab_while_media_is_not_shown_does_not_restore(window):
    """N1: a hidden page forgets the tab; it must not navigate to a neighbour."""
    open_title(window, movie(1, "One"))
    open_title(window, movie(2, "Two"))
    assert window._current_item.title == "Two"

    window.set_visible(False)
    pump(0.02)
    window._close_tab(window._tabs.active_id)
    pump(0.02)

    assert len(window._tabs) == 1
    assert window._tabs.active.key == "movie:1"
    # No restore happened: the closed tab's page is exactly where it was.
    assert window._current_item.title == "Two"


def test_switching_tabs_carries_a_verified_anime_session(window):
    """N5: a verified session travels with its tab, not with the window."""
    open_title(window, movie(1, "Frieren", "tv"))
    session = {
        "kind": "tmdb",
        "item": window._current_item,
        "season": 1,
        "episode": None,
        "route": object(),
        "provider": "ani",
        "number": 5,
    }
    window._forced_playback = dict(session)
    open_title(window, movie(2, "Another", "tv"))

    saved = tab(window, "tv:1")
    assert saved.payload["verified_playback"]["number"] == 5

    # PR #126 tears the live session down with the player it belonged to.
    window._forced_playback = None
    refreshed: list[bool] = []
    window._refresh_provider_button = lambda: refreshed.append(True)

    window._show_tab(saved.tab_id)
    pump(0.02)

    assert window._forced_playback == session
    assert refreshed


def test_switching_to_a_tab_without_a_session_clears_the_verified_context(window):
    """R1: ``_forced_playback`` follows the tab, so no session outlives it."""
    open_title(window, movie(1, "Frieren", "tv"))
    session = {
        "kind": "tmdb",
        "item": window._current_item,
        "season": 1,
        "episode": None,
        "route": object(),
        "provider": "ani",
        "number": 5,
    }
    window._forced_playback = dict(session)
    open_title(window, movie(2, "Another", "tv"))
    assert tab(window, "tv:1").payload["verified_playback"]["number"] == 5

    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)
    assert window._forced_playback == session
    assert window._current_provider() == "ani"
    assert "ani" in window._provider_choices()

    window._show_tab(tab(window, "tv:2").tab_id)
    pump(0.02)
    assert window._forced_playback is None, "the left tab's session stayed active"
    # The badge and menu now name the tab in front, not the one just left.
    assert window._current_provider() == window._streaming_provider()
    assert tuple(window._provider_choices()) == tuple(streaming.PROVIDERS)
    label = movies_ui.streaming.LABELS.get(window._streaming_provider())
    assert window._provider_label.get_text() == label


def test_a_session_belongs_only_to_the_tab_that_plays_it(window):
    """R1: another title's session is never written into the active tab."""
    open_title(window, movie(1, "One"))
    window._forced_playback = {
        "kind": "tmdb",
        "item": movie(99, "Somewhere Else"),
        "season": 1,
        "episode": None,
        "route": object(),
        "provider": "ani",
    }

    window._capture_active_tab()

    assert "verified_playback" not in tab(window, "movie:1").payload


def test_a_cleared_session_is_not_resurrected_by_a_later_switch(window):
    """F1: the one genuine teardown clears the tab's saved copy too."""
    open_title(window, movie(1, "Frieren", "tv"))
    window._forced_playback = verified_session(window)
    open_title(window, movie(2, "Another", "tv"))
    assert "verified_playback" in tab(window, "tv:1").payload

    # Back to the anime tab; its session comes with it.
    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)
    assert window._forced_playback is not None

    # The session genuinely ends (for example an unverified play replaces it),
    # so the tab's saved copy goes with it and cannot come back on a return.
    window._end_verified_session()
    assert window._forced_playback is None
    assert "verified_playback" not in tab(window, "tv:1").payload
    window._show_tab(tab(window, "tv:2").tab_id)
    pump(0.02)
    assert "verified_playback" not in tab(window, "tv:1").payload

    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)
    assert window._forced_playback is None


def test_capturing_a_tab_again_keeps_its_verified_session(window):
    """F1: a player teardown is not the end of a tab's verified session."""
    open_title(window, movie(1, "Frieren", "tv"))
    window._forced_playback = verified_session(window)
    window._stack.set_visible_child_name("player")
    window._capture_active_tab()
    assert tab(window, "tv:1").payload["verified_playback"]["number"] == 5

    # Back to details releases only the live context, not the session.
    window._stack.set_visible_child_name("details")
    assert window._forced_playback is None
    window._capture_active_tab()

    saved = tab(window, "tv:1").payload["verified_playback"]
    assert saved["provider"] == "ani" and saved["number"] == 5
    # The badge and menu still offer the anime providers the tab's session
    # uses; the saved anime preference itself is unchanged.
    assert window._provider_section() == "anime"
    assert "ani" in window._provider_choices()
    assert window._current_provider() == window._anime_provider()


def test_back_then_another_title_keeps_the_anime_session(window):
    """F1: Back, then opening another title, leaves the anime tab intact."""
    open_title(window, movie(1, "Frieren", "tv"))
    window._forced_playback = verified_session(window)
    window._playback_url = "https://provider.test/1"
    window._playback_provider = "ani"
    window._playback_key = "tv:1"
    window._stack.set_visible_child_name("player")
    window._back_target = "details"

    window._navigate_back()
    pump(0.02)
    assert window._stack.get_visible_child_name() == "details"
    assert window._forced_playback is None

    open_title(window, movie(2, "Another", "tv"))
    assert tab(window, "tv:1").payload["verified_playback"]["provider"] == "ani"
    # The new tab is in front; the anime tab's session is saved with it only.
    assert window._forced_playback is None

    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)
    assert window._forced_playback["provider"] == "ani"
    assert window._current_provider() == "ani"
    assert "ani" in window._provider_choices()


def test_leaving_media_and_resuming_keeps_the_anime_session(window):
    """F1: leaving Media and pressing Resume re-arms the verified session."""
    open_title(window, movie(1, "Frieren", "tv"))
    open_title(window, movie(2, "Another", "tv"))
    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)
    window._forced_playback = verified_session(window)
    window._playback_url = "https://provider.test/1"
    window._playback_provider = "ani"
    window._playback_key = "tv:1"
    window._playback_position = 300.0
    window._stack.set_visible_child_name("player")

    window.leave_view()
    pump(0.02)

    assert window._forced_playback["provider"] == "ani"
    assert window._current_provider() == "ani"

    plays: list[str] = []
    window._open_player = lambda url, title, **kwargs: plays.append(url)
    resume = [b for b in buttons(window._player) if b.get_label() == "Resume"][0]
    resume.clicked()

    assert plays == ["https://provider.test/1"]
    assert window._forced_playback["number"] == 5

    window._show_tab(tab(window, "tv:2").tab_id)
    pump(0.02)
    assert window._forced_playback is None

    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)
    assert window._forced_playback["provider"] == "ani"


def test_closing_the_last_tab_clears_the_verified_session(window):
    """F2: with no tab open, no verified session or anime menu may stay."""
    open_title(window, movie(1, "Frieren", "tv"))
    window._forced_playback = verified_session(window)
    assert window._current_provider() == "ani"

    window._close_tab(window._tabs.active_id)
    pump(0.02)

    assert window._forced_playback is None
    assert window._current_provider() == window._streaming_provider()
    assert tuple(window._provider_choices()) == tuple(streaming.PROVIDERS)
    assert window._provider_label.get_text() == streaming.LABELS.get(
        window._streaming_provider()
    )


def test_provider_badge_follows_the_tab_not_the_rail(window):
    """F3: an anime tab keeps anime providers while the rail says Home."""
    from lgl import anipm

    window._open_anime_details(anipm.AniTitle(anilist_id=7, title="Frieren"))
    pump(0.02)
    anime = window._tabs.active
    window._activate_nav("home")
    pump(0.02)
    open_title(window, movie(2, "Dune"))
    assert window._active_section == "home"

    window._show_tab(anime.tab_id)
    pump(0.02)

    assert window._active_section == "home", "the rail is not the section truth"
    assert window._provider_section() == "anime"
    assert "ani" in window._provider_choices()
    assert window._provider_label.get_text() == streaming.LABELS.get(
        window._current_provider()
    )


class PositionView:
    """A stand-in WebKit view: it answers only when the test says so."""

    def __init__(self, seconds: float = 0.0) -> None:
        self.seconds = seconds
        self.questions: list = []

    def run_javascript(self, _script, _cancellable, callback, _data) -> None:
        self.questions.append(callback)

    def run_javascript_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return self

    def get_js_value(self):
        return self

    def to_double(self) -> float:
        return self.seconds

    def answer(self, index: int = -1) -> None:
        self.questions[index](self, None)


def test_a_position_answer_from_a_closed_tab_is_ignored(window):
    """R2: a released view's answer cannot overwrite the saved position."""
    open_title(window, movie(1, "One"))
    open_title(window, movie(2, "Two"))
    window._stack.set_visible_child_name("player")
    window._webview = view = PositionView(754.0)
    window._playback_url = "https://provider.test/2"
    window._playback_key = "movie:2"
    window._playback_position = 754.0
    window._request_position()

    window._show_tab(tab(window, "movie:1").tab_id)
    pump(0.02)
    assert window._webview is None

    view.answer()
    assert window._playback_position == pytest.approx(754.0)
    assert tab(window, "movie:2").position == pytest.approx(754.0)


def test_a_position_answer_from_a_previous_view_is_ignored(window):
    """R2: only the view in the player may report a position."""
    window._webview = old = PositionView(754.0)
    window._request_position()

    window._webview = new = PositionView(100.0)
    window._request_position()

    old.answer()
    assert window._playback_position == 0.0

    new.answer()
    assert window._playback_position == pytest.approx(100.0)


def test_a_superseded_position_answer_is_ignored(window):
    """R2: an older question cannot overwrite a newer answer."""
    window._webview = view = PositionView(10.0)
    window._request_position()
    view.seconds = 20.0
    window._request_position()

    view.answer(0)
    assert window._playback_position == 0.0

    view.answer(1)
    assert window._playback_position == pytest.approx(20.0)


def test_a_position_answer_that_fails_is_ignored(window):
    """R2: a torn-down engine's error is not a position."""
    window._webview = view = PositionView(90.0)
    window._request_position()

    view.questions[-1](view, RuntimeError("the view was released"))
    assert window._playback_position == 0.0

    view.answer(0)
    assert window._playback_position == pytest.approx(90.0)


@pytest.mark.skipif(
    not hasattr(movies_ui.MoviesWindow, "_switch_forced_provider"),
    reason="PR #126's session-scoped provider switch is not on this branch yet",
)
def test_provider_switch_after_a_tab_round_trip_keeps_the_mapped_episode(window):
    """N5: after a return, a provider pick still switches the same session."""
    open_title(window, movie(1, "Frieren", "tv"))
    session = {
        "kind": "tmdb",
        "item": window._current_item,
        "season": 1,
        "episode": None,
        "route": object(),
        "provider": "ani",
        "number": 5,
    }
    window._forced_playback = dict(session)
    open_title(window, movie(2, "Another", "tv"))
    window._forced_playback = None
    window._show_tab(tab(window, "tv:1").tab_id)
    pump(0.02)

    calls: list[str] = []
    window._switch_forced_provider = lambda provider: calls.append(provider) or True
    window._refresh_provider_button = lambda: None
    window._choose_provider("toflix")

    assert calls == ["toflix"]
    assert window._forced_playback["number"] == 5


def test_home_offers_its_own_providers_not_a_tabs_saved_anime_session(window):
    """N1: a tab's verified session is not the Home page's provider section."""
    from lgl import anipm

    window._open_anime_details(anipm.AniTitle(anilist_id=7, title="Frieren"))
    pump(0.02)
    anime = window._tabs.active
    window._forced_playback = verified_session(window)
    window._capture_active_tab()  # the session is saved with its own tab
    window._forced_playback = None  # its live context is gone

    window._activate_nav("home")
    pump(0.02)

    assert window._stack.get_visible_child_name() == "home"
    assert anime.payload["verified_playback"]["provider"] == "ani"
    assert window._provider_section() == "movies"
    assert tuple(window._provider_choices()) == tuple(streaming.PROVIDERS)
    assert window._current_provider() == window._streaming_provider()
    assert window._provider_label.get_text() == streaming.LABELS.get(
        window._streaming_provider()
    )

    # A pick here is the plain movie/TV preference; the anime one is untouched.
    window._choose_provider("toflix")
    assert window._settings.get("streaming_provider") == "toflix"
    assert not window._settings.get("anime_streaming_provider", "")
    assert window._provider_label.get_text() == streaming.LABELS.get("toflix")
    assert window._forced_playback is None, "a page pick armed the anime session"
    assert anime.payload["verified_playback"]["provider"] == "ani", (
        "a page pick rewrote the tab's session"
    )

    # The tab that owns the session still offers, and names, its own providers.
    window._show_tab(anime.tab_id)
    pump(0.02)
    assert window._stack.get_visible_child_name() == "details"
    assert window._provider_section() == "anime"
    assert window._current_provider() == "ani"
    assert not window._settings.get("anime_streaming_provider", "")


class DeferredThread:
    """A route worker the test starts by hand, so the race is deterministic."""

    last = None

    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        DeferredThread.last = self

    def start(self):
        return None


def start_route(window, item, monkeypatch):
    """Ask for a play, but leave its route unresolved until the test says so."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        movies_ui, "threading", SimpleNamespace(Thread=DeferredThread)
    )
    window._play_routed(item, None, None)
    return DeferredThread.last


def finish_route(thread, route, monkeypatch) -> None:
    monkeypatch.setattr(
        movies_ui.gtk_playback, "decide_playback", lambda *_a, **_k: route
    )
    thread.target()
    pump(0.05)


def test_a_route_resolved_after_a_tab_switch_does_not_end_the_new_tabs_session(
    window, monkeypatch
):
    """N2: a stale route cannot clear the verified session in front."""
    from types import SimpleNamespace

    open_title(window, movie(1, "Frieren", "tv"))
    thread = start_route(window, window._current_item, monkeypatch)

    open_title(window, movie(2, "Another", "tv"))
    window._forced_playback = verified_session(window)
    window._capture_active_tab()
    assert "verified_playback" in tab(window, "tv:2").payload

    finish_route(
        thread,
        SimpleNamespace(
            mode=movies_ui.gtk_playback.MODE_ERROR, message="unresolved"
        ),
        monkeypatch,
    )

    assert window._forced_playback is not None
    assert "verified_playback" in tab(window, "tv:2").payload
    assert window._current_item.title == "Another"
    assert window._stack.get_visible_child_name() == "details"


def test_a_route_resolved_after_a_tab_switch_does_not_play_in_the_new_tab(
    window, monkeypatch
):
    """N2: a stale normal route does not start playback over another tab."""
    from types import SimpleNamespace

    open_title(window, movie(1, "One"))
    thread = start_route(window, window._current_item, monkeypatch)

    open_title(window, movie(2, "Two"))
    played: list = []
    window._play_with_providers = lambda *a, **k: played.append(a)

    finish_route(
        thread, SimpleNamespace(mode=movies_ui.gtk_playback.MODE_NORMAL), monkeypatch
    )

    assert played == []
    assert window._current_item.title == "Two"
    assert window._stack.get_visible_child_name() == "details"


def test_closing_a_tab_repaints_the_provider_badge(window):
    """N3: a close that restores no page still repaints the badge."""
    open_title(window, movie(1, "One"))
    open_title(window, movie(2, "Two"))
    window._activate_nav("home")
    pump(0.02)

    real = window._refresh_provider_button
    draws: list[bool] = []

    def spy() -> None:
        draws.append(True)
        real()

    window._refresh_provider_button = spy
    window._close_tab(tab(window, "movie:1").tab_id)
    pump(0.02)

    assert draws, "the badge was not repainted after a background close"
    assert window._tabs.active.key == "movie:2"
    assert window._stack.get_visible_child_name() == "home"
    assert window._provider_section() == "movies"
    assert window._provider_label.get_text() == streaming.LABELS.get(
        window._streaming_provider()
    )


def click_tab(window, index: int) -> bool:
    """Press the left button on a tab the way a user does: its event box."""
    events = window._tab_row.get_children()[index]
    event = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    event.button = 1
    event.x = event.y = 5
    event.x_root = event.y_root = window.get_toplevel().get_position()[0] + 5
    event.time = Gtk.get_current_event_time()
    return bool(events.emit("button-release-event", event))


def playing_tab(window, key: str, *, url: str, position: float = 0.0) -> None:
    """Give an open tab a saved stream, as a tab left playing would have."""
    state = tab(window, key)
    item = state.payload.get("item")
    window._tabs.update(
        state.tab_id, season=1, episode=1, playing=True, url=url,
        position=position, provider="cinesrc", payload={"item": item},
    )
    window._render_tabs()
    pump(0.02)


def test_a_fullscreen_player_leaves_the_media_tab_strip_on_screen(window):
    """The strip stays at the top of the screen during fullscreen playback.

    This is the reported bug: the window was fullscreened and the video handed
    the whole screen, but the strip above it has to remain laid out and
    clickable.  ``get_visible()`` is not enough here, so the test checks the
    allocated geometry of the bar and of every tab.
    """
    open_title(window, movie(1, "Pixels"))
    open_title(window, movie(2, "Rick and Morty", "tv"))
    playing_tab(window, "movie:1", url="https://provider.test/1")
    playing_tab(window, "tv:2", url="https://provider.test/2", position=612.0)
    window._stack.set_visible_child_name("player")
    window._playback_url = "https://provider.test/1"
    window._playback_key = "movie:1"
    window._playback_provider = "cinesrc"
    fullscreen_calls: list[bool] = []
    toplevel = window.get_toplevel()
    request = toplevel.fullscreen

    def spy() -> None:
        fullscreen_calls.append(True)
        request()

    toplevel.fullscreen = spy

    window._set_player_fullscreen(True)
    pump(0.1)

    assert fullscreen_calls, "the window was never asked to go fullscreen"
    assert window._player_fullscreen is True
    # The app chrome is gone; the strip is not part of it.
    assert window._header.get_visible() is False
    alloc = window._tab_bar.get_allocation()
    assert window._tab_bar.get_mapped() is True
    assert alloc.height >= 40 and alloc.width > 0
    assert alloc.y == 0, "the strip must sit at the top of the fullscreen window"
    for tab_widget in window._tab_row.get_children():
        tab_alloc = tab_widget.get_allocation()
        assert tab_widget.get_mapped() is True
        assert tab_alloc.width >= 40 and tab_alloc.height >= 40
    # The video starts below the strip: they cannot cover each other.
    assert alloc.y + alloc.height <= window._stack.get_allocation().y


def test_switching_tabs_during_fullscreen_keeps_fullscreen_and_playback_state(window):
    open_title(window, movie(1, "Pixels"))
    open_title(window, movie(2, "Rick and Morty", "tv"))
    window._show_tab(tab(window, "movie:1").tab_id)
    pump(0.05)
    window._stack.set_visible_child_name("player")
    window._playback_url = "https://provider.test/1"
    window._playback_key = "movie:1"
    window._playback_provider = "cinesrc"
    playing_tab(window, "movie:1", url="https://provider.test/1")
    # The other tab has a saved stream of its own: the screenshot's case.
    playing_tab(window, "tv:2", url="https://provider.test/2", position=612.0)
    window._set_player_fullscreen(True)
    pump(0.1)

    assert click_tab(window, 1) is True, "the tab was not clickable in fullscreen"
    pump(0.1)

    # The click switched tabs and did not drop out of fullscreen.
    assert window._tabs.active.key == "tv:2"
    assert window._player_fullscreen is True
    assert window._header.get_visible() is False
    assert window._tab_bar.get_mapped() is True
    assert window._tab_bar.get_allocation().y == 0
    # The tab that was left kept its stream and its place.
    left = tab(window, "movie:1")
    assert left.playing is True
    assert left.url == "https://provider.test/1"
    # The tab switched to shows its paused panel, readable, not blank.
    assert window._stack.get_visible_child_name() == "player"
    assert window._webview is None
    assert any("10:12" in text for text in label_texts(window._player))
    assert all(child.get_visible() for child in window._player.get_children())


def test_a_fullscreen_resume_keeps_the_tab_strip_and_hides_the_page_chrome(window):
    open_title(window, movie(1, "Pixels"))
    open_title(window, movie(2, "Rick and Morty", "tv"))
    window._show_tab(tab(window, "movie:1").tab_id)
    pump(0.05)
    window._stack.set_visible_child_name("player")
    window._playback_url = "https://provider.test/1"
    window._playback_key = "movie:1"
    playing_tab(window, "movie:1", url="https://provider.test/1")
    playing_tab(
        window, "tv:2",
        url="https://cinesrc.st/embed/tv/2?back=close", position=612.0,
    )
    window._set_player_fullscreen(True)
    pump(0.05)
    click_tab(window, 1)
    pump(0.05)

    window._resume_tab(tab(window, "tv:2"))
    pump(0.1)

    assert window._player_fullscreen is True
    assert window._stack.get_visible_child_name() == "player"
    assert window._player_frame is not None
    assert window._tab_bar.get_mapped() is True
    assert window._tab_bar.get_allocation().y == 0
    # Only the video is on the page: title and notes are hidden again.
    for child in window._player.get_children():
        if child is window._player_frame:
            continue
        assert child.get_visible() is False


def test_leaving_fullscreen_restores_the_normal_media_layout(window):
    open_title(window, movie(1, "Pixels"))
    open_title(window, movie(2, "Rick and Morty", "tv"))
    playing_tab(window, "movie:1", url="https://provider.test/1")
    window._stack.set_visible_child_name("player")
    window._set_player_fullscreen(True)
    pump(0.05)
    assert window._tab_bar.get_allocation().y == 0

    window._set_player_fullscreen(False)
    pump(0.05)

    assert window._player_fullscreen is False
    assert window._header.get_visible() is True
    bar = window._tab_bar.get_allocation()
    # Under the header again, and still laid out.
    assert bar.y == window._header.get_allocation().height
    assert bar.height >= 40 and bar.width > 0
    assert [item.title for item in window._tabs] == ["Pixels", "Rick and Morty"]


def test_closing_the_active_tab_in_fullscreen_restores_the_normal_layout(window):
    open_title(window, movie(1, "Pixels"))
    open_title(window, movie(2, "Rick and Morty", "tv"))
    window._show_tab(tab(window, "tv:2").tab_id)
    pump(0.05)
    playing_tab(window, "tv:2", url="https://provider.test/2", position=90.0)
    window._stack.set_visible_child_name("player")
    window._set_player_fullscreen(True)
    pump(0.05)
    assert window._player_fullscreen is True

    window._close_tab(tab(window, "tv:2").tab_id)
    pump(0.1)

    # The video is gone, so the normal layout comes back with the strip in it.
    assert window._player_fullscreen is False
    assert window._header.get_visible() is True
    assert window._tab_bar.get_mapped() is True
    assert window._tab_bar.get_allocation().height >= 40
    assert [item.title for item in window._tabs] == ["Pixels"]


def test_opening_another_title_during_fullscreen_returns_to_the_normal_layout(window):
    open_title(window, movie(1, "Pixels"))
    playing_tab(window, "movie:1", url="https://provider.test/1")
    window._stack.set_visible_child_name("player")
    window._set_player_fullscreen(True)
    pump(0.05)

    open_title(window, movie(3, "Severance", "tv"))
    pump(0.1)

    assert window._tabs.active.key == "tv:3"
    assert window._player_fullscreen is False
    assert window._header.get_visible() is True
    assert window._tab_bar.get_mapped() is True
    assert window._tab_bar.get_allocation().height >= 40


def test_closing_a_paused_tab_in_fullscreen_restores_the_normal_layout(window):
    """Closing the tab in front ends fullscreen, paused or not.

    Qt does the same: the paused panel is not the video the whole screen was
    given to, so the header and the strip come back with the next tab.
    """
    open_title(window, movie(1, "Pixels"))
    open_title(window, movie(2, "Rick and Morty", "tv"))
    window._show_tab(tab(window, "movie:1").tab_id)
    pump(0.05)
    window._stack.set_visible_child_name("player")
    window._playback_url = "https://provider.test/1"
    window._playback_key = "movie:1"
    playing_tab(window, "movie:1", url="https://provider.test/1")
    # The paused tab keeps its saved stream, so the switch to it is the one
    # the bug was about.
    playing_tab(window, "tv:2", url="https://provider.test/2", position=90.0)
    window._set_player_fullscreen(True)
    pump(0.05)
    # The tab in front now is paused, still inside the fullscreen window.
    click_tab(window, 1)
    pump(0.05)
    assert window._player_fullscreen is True
    assert window._tabs.active.key == "tv:2"

    window._close_tab(tab(window, "tv:2").tab_id)
    pump(0.1)

    assert window._player_fullscreen is False
    assert window._header.get_visible() is True
    assert window._tab_bar.get_mapped() is True
    assert window._tab_bar.get_allocation().height >= 40
    assert [item.title for item in window._tabs] == ["Pixels"]
