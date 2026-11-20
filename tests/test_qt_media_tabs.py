"""Browser-style media tabs in the Qt frontend.

Every test builds real ``MediaPage`` tab pages offscreen with a stub web view,
so no TMDB call and no WebEngine is involved: what is tested is the tab
behaviour — deduplication, switching, what a switch saves, and what closing a
tab does to the players around it.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget  # noqa: E402

from lgl import qt_media, qt_media_tabs  # noqa: E402
from lgl.qt_player import (  # noqa: E402
    EmbeddedPlayer,
    end_window_fullscreen,
    in_window_fullscreen,
)
from lgl.settings import Settings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app: QApplication, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


class StubWebPage:
    """The QWebEnginePage stand-in: answers ``currentTime`` when asked."""

    def __init__(self, view: "StubView") -> None:
        self.view = view
        self.scripts: list[str] = []

    def runJavaScript(self, script: str, callback) -> None:  # noqa: N802 - Qt's name
        self.scripts.append(script)
        callback(self.view.position)


class StubView(QWidget):
    """A web view with no engine behind it, counting what the player asked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.urls: list[str] = []
        self.stopped = 0
        self.position = 0.0
        self._page = StubWebPage(self)

    def setUrl(self, url) -> None:  # noqa: N802 - Qt's name
        self.urls.append(url.toString())

    def page(self):
        return self._page

    def stop(self) -> None:
        self.stopped += 1


class PlayerFactory:
    """Builds real players around stub views and keeps them for inspection."""

    def __init__(self) -> None:
        self.views: list[StubView] = []
        self.players: list[EmbeddedPlayer] = []

    def __call__(self, parent: QWidget) -> EmbeddedPlayer:
        player = EmbeddedPlayer(parent, view_factory=self._view)
        self.players.append(player)
        return player

    def _view(self, parent: QWidget):
        view = StubView(parent)
        self.views.append(view)
        return view

    def release_count(self) -> int:
        return sum(1 for view in self.views if view.stopped)


@pytest.fixture
def tabs(app, tmp_path):
    stack = QStackedWidget()
    stack.resize(900, 600)
    stack.show()
    bar = qt_media_tabs.MediaTabBar()
    factory = PlayerFactory()
    closed: list[bool] = []
    controller = qt_media_tabs.MediaTabsController(
        stack, bar, settings=Settings(tmp_path / "config.json", keyring=False),
        cache_root=tmp_path, player_factory=factory,
        on_empty=lambda: closed.append(True),
    )
    controller.closed_marker = closed
    yield controller, stack, bar, factory
    controller.close_all()


def card(key: str, title: str, kind: str = "movie") -> qt_media.Card:
    return qt_media.Card(key=key, title=title, kind=kind, year="2020")


def test_opening_media_creates_a_tab_with_a_title_and_subtitle(tabs):
    controller, _stack, bar, _factory = tabs
    controller.open(card("movie:1", "Interstellar"))
    assert bar.count() == 1
    button = bar.buttons()[0]
    assert button.title_label.text() == "Interstellar"
    assert button.meta_label.text() == "Movie"


def test_opening_an_open_title_selects_it_instead_of_duplicating(tabs):
    controller, stack, bar, _factory = tabs
    first = controller.open(card("tv:2", "Breaking Bad", "tv"))
    second = controller.open(card("tv:2", "Breaking Bad", "tv"))
    assert bar.count() == 1
    assert first is second
    assert stack.currentWidget() is first


def test_switching_movie_and_tv_tabs_keeps_the_episode_and_scroll(tabs, app):
    controller, stack, bar, _factory = tabs
    movie = controller.open(card("movie:1", "Interstellar"))
    show = controller.open(card("tv:2", "Breaking Bad", "tv"))
    show._playing_episode = (2, 5)  # noqa: SLF001 - the page's own selection
    # A real page is taller than the viewport; the offscreen one needs telling.
    show._detail_page.widget().setMinimumHeight(3000)  # noqa: SLF001
    pump(app)
    show.set_scroll_position(140)
    assert show.scroll_position() == 140
    controller.activate(controller.tabs[0].tab_id)
    assert stack.currentWidget() is movie
    controller.activate(controller.tabs[1].tab_id)
    assert stack.currentWidget() is show
    assert show._playing_episode == (2, 5)  # noqa: SLF001
    assert show.scroll_position() == 140
    assert bar.buttons()[1].meta_label.text() == "S2 · EP5"


def test_switching_tabs_pauses_and_releases_the_inactive_player(tabs):
    controller, _stack, bar, factory = tabs
    controller.open(card("movie:1", "Interstellar"))
    show = controller.open(card("tv:2", "Breaking Bad", "tv"))
    show._playing_episode = (2, 5)  # noqa: SLF001 - the page's own selection
    show.play_embedded("https://provider.test/watch/2", "Breaking Bad")
    assert show.player.is_playing() is True
    view = show.player.view
    view.position = 42.0

    controller.activate(controller.tabs[0].tab_id)

    tab = controller.store.find("tv:2")
    assert tab.playing is True
    assert tab.position == pytest.approx(42.0)
    assert tab.url == "https://provider.test/watch/2"
    # The expensive view is gone, and the page remembers where it was.
    assert show.player.view is None
    assert show.player.is_playing() is False
    assert view.stopped >= 1
    assert bar.buttons()[1].meta_label.text() == "S2 · EP5"


def test_returning_to_a_tab_restores_its_episode_and_position_paused(tabs):
    controller, stack, bar, factory = tabs
    controller.open(card("movie:1", "Interstellar"))
    show = controller.open(card("tv:2", "Breaking Bad", "tv"))
    show._playing_episode = (2, 5)  # noqa: SLF001 - the page's own selection
    show.play_embedded("https://provider.test/watch/2", "Breaking Bad")
    show.player.view.position = 754.0
    controller.activate(controller.tabs[0].tab_id)

    controller.activate(controller.tabs[1].tab_id)
    # The page is back, with its episode still selected.
    assert stack.currentWidget() is show
    assert show.detail_card().title == "Breaking Bad"
    # ...and the player is paused at the saved position, not playing.
    assert show.player.is_playing() is False
    assert show.player.pages.currentWidget() is show.player.paused
    assert "12:34" in show.player.paused_note.text()
    assert show.player.view is None

    show.player.resume()
    assert show.player.is_playing() is True
    assert show.player.view is not None
    assert show.player.view.urls[-1] == "https://provider.test/watch/2#t=754"
    assert show.player._position == pytest.approx(754.0)  # noqa: SLF001


def test_a_tabs_back_returns_to_the_hub_and_keeps_the_tab(tabs):
    controller, stack, bar, _factory = tabs
    hub = QWidget()
    stack.addWidget(hub)
    stack.setCurrentWidget(hub)
    hub_requests: list[bool] = []
    controller.on_hub = lambda: hub_requests.append(True)

    page = controller.open(card("movie:1", "Interstellar"))
    page.show_section("home")  # what the detail page's Back button calls

    assert hub_requests == [True]
    assert bar.count() == 1
    assert controller.active_tab().key == "movie:1"


def test_closing_the_active_tab_selects_a_neighbour(tabs):
    controller, stack, _bar, _factory = tabs
    controller.open(card("movie:1", "One"))
    controller.open(card("movie:2", "Two"))
    last = controller.open(card("movie:3", "Three"))
    middle_tab = controller.tabs[1]
    controller.close(middle_tab.tab_id)
    assert controller.active_id == controller.tabs[1].tab_id
    assert stack.currentWidget() is last


def test_closing_an_inactive_tab_does_not_interrupt_the_active_one(tabs):
    controller, stack, bar, _factory = tabs
    controller.open(card("movie:1", "One"))
    playing = controller.open(card("movie:2", "Two"))
    playing.play_embedded("https://provider.test/two", "Two")
    assert playing.player.is_playing() is True

    controller.close(controller.tabs[0].tab_id)

    assert bar.count() == 1
    assert stack.currentWidget() is playing
    assert playing.player.is_playing() is True
    assert playing.player.view is not None


def test_closing_the_last_tab_returns_to_the_default_page(tabs):
    controller, _stack, bar, _factory = tabs
    controller.open(card("movie:1", "One"))
    controller.close(controller.tabs[0].tab_id)
    assert len(controller.tabs) == 0
    assert controller.closed_marker == [True]
    assert bar.count() == 0


def test_closing_a_tab_stops_its_playback(tabs):
    controller, _stack, _bar, _factory = tabs
    page = controller.open(card("movie:1", "One"))
    page.play_embedded("https://provider.test/one", "One")
    view = page.player.view
    controller.close(controller.tabs[0].tab_id)
    assert view.stopped >= 1
    assert page.player.is_playing() is False


def test_tabs_stay_visible_while_other_pages_are_open(app, tmp_path):
    from lgl import qt_main

    qt_main.apply_theme(app, "dark")
    window = qt_main.Window(
        None, None, None, tmp_path, Settings(tmp_path / "config.json", keyring=False)
    )
    window.show()
    pump(app)
    try:
        window.media.open_card(card("movie:9", "Interstellar"))
        pump(app)
        assert window.media_tabs_bar.isVisible() is True
        assert window.media_tabs_bar.count() == 1
        tab_id = window.media_tabs.active_id

        window.show_view("browse")
        pump(app)
        assert window.media_tabs_bar.isVisible() is True
        assert window._current == "browse"  # noqa: SLF001

        window.media_tabs.activate(tab_id)
        pump(app)
        assert window.stack.currentWidget() is window.media_tabs.page_for(tab_id)
        assert window._current == "media"  # noqa: SLF001
    finally:
        window.close()


@pytest.fixture
def qt_window(app, tmp_path):
    """A real window, with stub players behind every tab page."""
    from lgl import qt_main

    qt_main.apply_theme(app, "dark")
    window = qt_main.Window(
        None, None, None, tmp_path, Settings(tmp_path / "config.json", keyring=False)
    )
    factory = PlayerFactory()
    # Only the tabs get stub views; the hub keeps its own player.
    window.media_tabs.player_factory = factory
    window.show()
    pump(app)
    window.stub_factory = factory
    try:
        yield window
    finally:
        window.close()
        pump(app, 0.02)


def test_leaving_the_media_view_suspends_the_tabs_player(qt_window, app):
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    view = page.player.view
    view.position = 120.0
    assert page.player.is_playing() is True

    window.show_view("browse")
    pump(app)

    tab = window.media_tabs.store.find("movie:9")
    assert tab.playing is True
    assert tab.url == "https://provider.test/watch/9"
    assert tab.position == pytest.approx(120.0)
    # Nothing is left loaded, so no page can keep playing in the background.
    assert page.player.view is None
    assert view.stopped >= 1
    assert page.player.is_playing() is False

    window.show_view("media")
    pump(app)

    # Returning restores the page and the position, paused until Resume.
    assert window.stack.currentWidget() is page
    assert page.player.pages.currentWidget() is page.player.paused
    assert page.player.view is None
    assert page.player.is_playing() is False
    assert "2:00" in page.player.paused_note.text()


def test_leaving_the_media_view_twice_stays_suspended(qt_window, app):
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    window.show_view("browse")
    pump(app)
    window.show_view("library")
    pump(app)

    assert page.player.is_playing() is False
    assert page.player.view is None
    assert window.media_tabs.showing is False


def test_back_leaving_media_suspends_a_playing_tab(qt_window, app):
    """B1: the header's Back runs the same cleanup as the rail."""
    window = qt_window
    window.show_view("browse")
    pump(app)
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    window.show_view("media")
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    view = page.player.view
    view.position = 90.0
    assert page.player.is_playing() is True

    window.go_back()
    pump(app)

    assert window._current == "browse"  # noqa: SLF001
    assert window.stack.currentWidget() is window.browse
    # Nothing is left loaded, so no page can keep playing behind Browse.
    assert page.player.is_playing() is False
    assert page.player.view is None
    assert view.stopped >= 1
    saved = window.media_tabs.store.find("movie:9")
    assert saved.playing is True
    assert saved.position == pytest.approx(90.0)

    # Forward brings the same tab back, paused, with nothing autoplaying.
    window.go_forward()
    pump(app)

    assert window._current == "media"  # noqa: SLF001
    assert window.stack.currentWidget() is page
    assert page.player.is_playing() is False
    assert page.player.view is None
    assert page.player.pages.currentWidget() is page.player.paused
    assert "1:30" in page.player.paused_note.text()


def test_forward_leaving_media_suspends_a_playing_tab(qt_window, app):
    """B1: the header's Forward cleans up too, not only Back."""
    window = qt_window
    window.show_view("browse")
    pump(app)
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    window.show_view("media")
    pump(app)
    window.show_view("library")
    pump(app)

    window.go_back()
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    assert window.stack.currentWidget() is page
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    view = page.player.view
    assert page.player.is_playing() is True

    window.go_forward()
    pump(app)

    assert window._current == "library"  # noqa: SLF001
    assert window.stack.currentWidget() is window.library
    assert page.player.is_playing() is False
    assert page.player.view is None
    assert view.stopped >= 1


def test_leaving_media_by_history_stops_the_hub_player(qt_window, app):
    """B1: a hub stream, not a tab, is released when Back leaves Media."""
    window = qt_window
    hub = EmbeddedPlayer(window.media, view_factory=window.stub_factory._view)
    window.media.player = hub
    window.show_view("browse")
    pump(app)
    window.show_view("media")
    pump(app)
    window.media.play_embedded("https://provider.test/hub", "Hub title")
    pump(app)
    view = hub.view
    assert hub.is_playing() is True

    window.go_back()
    pump(app)

    assert window._current == "browse"  # noqa: SLF001
    assert window.stack.currentWidget() is window.browse
    assert hub.is_playing() is False
    assert hub.view is None
    assert view.stopped >= 1


def test_two_tabs_cannot_play_at_the_same_time(tabs):
    controller, stack, _bar, factory = tabs
    first = controller.open(card("movie:1", "One"))
    first.play_embedded("https://provider.test/one", "One")
    second = controller.open(card("movie:2", "Two"))
    second.play_embedded("https://provider.test/two", "Two")

    playing = [player for player in factory.players if player.is_playing()]
    assert playing == [second.player]
    assert first.player.is_playing() is False
    assert first.player.view is None


def test_closing_the_last_tab_while_another_page_is_visible_stays_there(
    qt_window, app
):
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    window.show_view("browse")
    pump(app)

    window.media_tabs.close(window.media_tabs.active_id)
    pump(app)

    assert window.media_tabs_bar.isVisible() is False
    assert window.stack.currentWidget() is window.browse
    assert window._current == "browse"  # noqa: SLF001


def test_a_paused_tab_keeps_its_stream_across_switches(tabs):
    controller, _stack, _bar, _factory = tabs
    controller.open(card("movie:1", "Interstellar"))
    show = controller.open(card("tv:2", "Breaking Bad", "tv"))
    show._playing_episode = (2, 5)  # noqa: SLF001 - the page's own selection
    show.play_embedded("https://provider.test/watch/2", "Breaking Bad")
    show.player.view.position = 754.0
    controller.activate(controller.tabs[0].tab_id)
    controller.activate(controller.tabs[1].tab_id)
    assert show.player.is_playing() is False  # the panel, not the video

    # Away again without pressing Resume: the saved stream must survive.
    controller.activate(controller.tabs[0].tab_id)
    tab = controller.store.find("tv:2")
    assert tab.url == "https://provider.test/watch/2"
    assert tab.position == pytest.approx(754.0)
    assert tab.playing is True

    controller.activate(controller.tabs[1].tab_id)
    assert show.player.pages.currentWidget() is show.player.paused
    assert "12:34" in show.player.paused_note.text()


def test_next_episode_survives_a_tab_round_trip(tabs):
    from types import SimpleNamespace

    controller, _stack, _bar, _factory = tabs
    controller.open(card("movie:1", "Interstellar"))
    show = controller.open(card("tv:2", "Breaking Bad", "tv"))
    show._playing_episode = (1, 1)  # noqa: SLF001
    show._next_episode = lambda: (1, SimpleNamespace(number=2, title="Cat"))  # noqa: SLF001
    show._refresh_next_episode()  # noqa: SLF001
    assert show.player.next_episode.isHidden() is False

    controller.activate(controller.tabs[0].tab_id)
    controller.activate(controller.tabs[1].tab_id)

    assert show.player.next_episode.isHidden() is False
    assert "S01E02" in show.player.next_episode.toolTip()
    requests: list[bool] = []
    show.player.next_requested.connect(lambda: requests.append(True))
    show.player.next_episode.click()
    assert requests == [True]


def test_rapid_switching_and_closing_releases_every_view(tabs):
    controller, _stack, bar, factory = tabs
    pages = [controller.open(card(f"movie:{n}", f"Title {n}")) for n in (1, 2, 3)]
    for page in pages:
        key = page.detail_card().key
        tab = controller.store.find(key)
        controller.activate(tab.tab_id)
        page.play_embedded(f"https://provider.test/{key}", tab.title)
        page.player.view.position = float(10 * len(factory.views))

    assert sum(1 for player in factory.players if player.is_playing()) == 1
    for tab in controller.tabs:
        controller.close(tab.tab_id)
    assert bar.count() == 0
    assert not any(player.is_playing() for player in factory.players)
    assert all(view.stopped for view in factory.views)


def test_a_released_view_cannot_report_a_position_later(tabs):
    controller, _stack, _bar, _factory = tabs
    page = controller.open(card("movie:1", "One"))
    page.player.play("https://provider.test/one", "One")
    page.player._position = 12.0  # noqa: SLF001 - the last live sample
    callbacks: list = []
    page.player.view.page().runJavaScript = (  # noqa: SLF001
        lambda script, callback: callbacks.append(callback)
    )
    page.player.capture_position()
    assert callbacks
    callbacks[0](30.0)
    assert page.player._position == pytest.approx(30.0)  # noqa: SLF001

    page.player.release_resources()
    callbacks[0](999.0)  # a late answer from the freed view
    assert page.player._position == pytest.approx(30.0)  # noqa: SLF001


class VerifiedSession:
    """Stand-in for PR #126's verified-playback record a tab must carry.

    The tab layer only transports it; #126's provider switch reads it back, so
    the mapped AniList episode and the session provider must survive a switch.
    """

    def __init__(self) -> None:
        self.title = "Frieren"
        self.anilist_id = 42
        self.ani_episode = 5
        self.provider = "ani"
        self.season = 1
        self.tmdb_episode = None


def test_home_hero_watch_plays_in_the_titles_tab_not_the_hub(
    qt_window, app, monkeypatch
):
    """B1: Watch Now must open the tab, and leaving Media must pause it."""
    window = qt_window
    # No identity lookup: this test is about which player starts.
    monkeypatch.setattr(qt_media.qt_anime_routing, "available", lambda: False)
    window.media._hero_card = qt_media.Card(
        key="movie:9", title="Interstellar", kind="movie", tmdb_id=9,
    )

    window.media._play_current()
    pump(app)

    tab_id = window.media_tabs.active_id
    assert tab_id is not None
    page = window.media_tabs.page_for(tab_id)
    assert page is not None and page is not window.media
    url = page.player.current_url()
    assert url
    # The stream started in the tab's player; the hub's never loaded anything.
    assert page.player.is_playing() is True
    assert window.media.player.is_playing() is False
    assert window.media.player.view is None

    window.show_view("browse")
    pump(app)

    saved = window.media_tabs.store.find("movie:9")
    assert saved.playing is True
    assert saved.url == url
    assert page.player.is_playing() is False
    assert page.player.view is None
    assert window.media.player.is_playing() is False

    window.show_view("media")
    pump(app)

    # Returning shows the same tab, paused, with nothing playing by itself.
    assert window.stack.currentWidget() is page
    assert page.player.is_playing() is False
    assert page.player.view is None
    assert window.media.player.is_playing() is False


def test_a_tab_snapshot_carries_a_verified_anime_session(tabs, app):
    """N5: the session is saved before the suspend tears it down."""
    controller, _stack, _bar, _factory = tabs
    first = controller.open(card("movie:9", "Frieren"))
    pump(app)
    session = VerifiedSession()
    first._verified_context = session
    first._forced_anime_playback = True

    def end_session() -> None:
        # PR #126's teardown forgets the live session, so the tab must already
        # own it when the page is suspended.
        first._forced_anime_playback = False
        first._verified_context = None

    first._end_forced_anime_session = end_session
    refreshed: list[str] = []
    original_fill = first._fill_provider_menu
    first._fill_provider_menu = (
        lambda section: (refreshed.append(section), original_fill(section))
    )

    controller.open(card("movie:10", "Another"))
    pump(app)

    saved = controller.store.find("movie:9")
    assert saved.payload["verified_anime"] is session
    assert saved.payload["forced_anime"] is True
    assert first._verified_context is None

    controller.activate(saved.tab_id)
    pump(app)

    # The session, its provider badge and its menu come back with the tab.
    assert first._verified_context is session
    assert first._verified_context.ani_episode == 5
    assert first._forced_anime_playback is True
    assert refreshed and refreshed[-1] == first._section


@pytest.mark.skipif(
    not hasattr(qt_media.MediaPage, "_switch_verified_session"),
    reason="PR #126's session-scoped provider switch is not on this branch yet",
)
def test_provider_switch_after_a_tab_round_trip_replays_the_anilist_episode(tabs, app):
    """N5: switching providers after a return uses the restored session."""
    controller, _stack, _bar, _factory = tabs
    page = controller.open(card("movie:9", "Frieren"))
    pump(app)
    page._verified_context = VerifiedSession()
    page._forced_anime_playback = True
    controller.open(card("movie:10", "Another"))
    pump(app)
    controller.activate(controller.store.find("movie:9").tab_id)
    pump(app)

    calls: list[str] = []
    page._switch_verified_session = lambda key: calls.append(key) or True
    page._choose_provider("toflix")

    assert calls == ["toflix"]
    assert page._verified_context.ani_episode == 5
    assert page._forced_anime_playback is True


def test_a_fullscreen_player_hides_the_media_tab_strip(qt_window, app):
    """Fullscreen shows the video and nothing else, the strip included.

    The strip is chrome: it goes with the header and the rail, and it comes
    back the moment fullscreen ends, without a tab operation first.
    """
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    pump(app)
    assert window.media_tabs_bar.isVisible() is True

    page.player.set_fullscreen(True)
    pump(app)

    assert window.isFullScreen() is True
    assert window.media_tabs_bar.isVisible() is False
    assert page.player.chrome.isHidden() is True

    page.player.set_fullscreen(False)
    pump(app)

    assert window.isFullScreen() is False
    assert window.media_tabs_bar.isVisible() is True
    assert window.media_tabs_bar.height() >= 36
    assert window.media_tabs_bar.count() == 1


def test_the_tab_strip_goes_only_once_the_window_is_fullscreen(qt_window, app, monkeypatch):
    """The strip is not hidden in the pass that calls ``showFullScreen()``.

    The strip sits in the layout that sizes the page stack, and the video's
    view lives in that stack.  Hiding the strip while the window was being
    fullscreened resized the video's surface mid-handoff, which is what left
    the player black or frozen.  It goes one moment later instead, still owned
    by the session so every exit path puts it back.
    """
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    pump(app)
    assert window.media_tabs_bar.isVisible() is True

    still_visible: list[bool] = []
    show_fullscreen = window.showFullScreen

    def record() -> None:
        still_visible.append(window.media_tabs_bar.isVisible())
        show_fullscreen()

    monkeypatch.setattr(window, "showFullScreen", record)
    page.player.set_fullscreen(True)
    pump(app)

    # At the handoff the strip is untouched; it is gone right after it.
    assert still_visible == [True]
    assert window.media_tabs_bar.isVisible() is False
    # The session keeps it, so leaving fullscreen restores it.
    assert (window.media_tabs_bar, True) in _session(window).hidden
    page.player.set_fullscreen(False)
    pump(app)
    assert window.media_tabs_bar.isVisible() is True


def test_entering_and_leaving_fullscreen_leaves_the_stream_untouched(qt_window, app):
    """The player keeps its view and its stream through a fullscreen round trip."""
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    pump(app)
    player = page.player
    view = player.view
    loaded = list(view.urls)
    assert player.is_playing() is True

    player.set_fullscreen(True)
    pump(app)

    # Same view, same URL, still the page in front: nothing was stopped or
    # rebuilt to make room for the strip going away.
    assert player.view is view
    assert view.urls == loaded
    assert player.is_playing() is True
    assert player.pages.currentWidget() is view
    assert view.isVisible() is True
    assert window.media_tabs_bar.isVisible() is False

    player.set_fullscreen(False)
    pump(app)

    assert player.view is view
    assert view.urls == loaded
    assert player.is_playing() is True
    assert view.isVisible() is True
    assert window.media_tabs_bar.isVisible() is True


def test_switching_tabs_in_fullscreen_keeps_fullscreen_and_saved_positions(qt_window, app):
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    first_id = window.media_tabs.active_id
    first = window.media_tabs.page_for(first_id)
    first.play_embedded("https://provider.test/watch/9", "Interstellar")
    first.player.view.position = 95.0
    pump(app)
    window.media.open_card(card("tv:2", "Breaking Bad", "tv"))
    pump(app)
    second_id = window.media_tabs.active_id
    second = window.media_tabs.page_for(second_id)
    second.play_embedded("https://provider.test/watch/2", "Breaking Bad")
    pump(app)

    window.media_tabs.activate(first_id)
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    assert window.isFullScreen() is True

    window.media_tabs.activate(second_id)
    pump(app)

    # Switching tabs does not leave fullscreen, and the strip stays hidden.
    assert window.isFullScreen() is True
    assert window.media_tabs_bar.isVisible() is False
    # The tab that was left is suspended with its stream and position saved.
    left = window.media_tabs.store.find("movie:9")
    assert left.playing is True
    assert left.url == "https://provider.test/watch/9"
    assert left.position == pytest.approx(95.0)
    assert first.player.is_playing() is False
    # The tab switched to comes back paused, inside the same fullscreen window.
    assert second.player.pages.currentWidget() is second.player.paused
    assert second.player.chrome.isHidden() is True

    # Resume continues in the same fullscreen window.
    second.player.resume()
    pump(app)
    assert second.player.is_playing() is True
    assert window.isFullScreen() is True
    assert second.player.chrome.isHidden() is True
    assert window.media_tabs_bar.isVisible() is False

    # Leaving fullscreen brings the strip straight back.
    second.player.set_fullscreen(False)
    pump(app)
    assert window.isFullScreen() is False
    assert window.media_tabs_bar.isVisible() is True


def test_escape_on_a_paused_tab_in_fullscreen_only_leaves_fullscreen(qt_window, app):
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    first_id = window.media_tabs.active_id
    first = window.media_tabs.page_for(first_id)
    first.play_embedded("https://provider.test/watch/9", "Interstellar")
    pump(app)
    window.media.open_card(card("tv:2", "Breaking Bad", "tv"))
    pump(app)
    second_id = window.media_tabs.active_id
    second = window.media_tabs.page_for(second_id)
    second.play_embedded("https://provider.test/watch/2", "Breaking Bad")
    pump(app)
    window.media_tabs.activate(first_id)
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    window.media_tabs.activate(second_id)
    pump(app)

    QTest.keyClick(second.player, Qt.Key_Escape)
    pump(app)

    assert window.isFullScreen() is False
    assert window.media_tabs_bar.isVisible() is True
    # The paused tab is still the page in front: Escape only left fullscreen.
    assert window.stack.currentWidget() is second
    assert second.player.pages.currentWidget() is second.player.paused


def test_closing_the_last_tab_in_fullscreen_leaves_fullscreen(qt_window, app):
    window = qt_window
    window.media.open_card(card("movie:9", "Interstellar"))
    pump(app)
    page = window.media_tabs.page_for(window.media_tabs.active_id)
    page.play_embedded("https://provider.test/watch/9", "Interstellar")
    pump(app)
    page.player.set_fullscreen(True)
    pump(app)
    assert window.isFullScreen() is True

    window.media_tabs.close(window.media_tabs.active_id)
    pump(app)

    assert window.isFullScreen() is False
    assert window.media_tabs_bar.isVisible() is False


def _playing_tab(window, app, which: qt_media.Card):
    """Open ``which`` in its own tab and play it; returns ``(tab_id, page)``."""
    window.media.open_card(which)
    pump(app)
    tab_id = window.media_tabs.active_id
    page = window.media_tabs.page_for(tab_id)
    page.play_embedded(f"https://provider.test/watch/{which.key}", which.title)
    pump(app)
    return tab_id, page


def test_switching_to_a_details_only_tab_leaves_fullscreen(qt_window, app):
    """B1: a tab with no stream must not keep the window fullscreen.

    The details page has no video and no Escape handler of its own, so staying
    fullscreen there left the header and rail hidden with no way back.
    """
    window = qt_window
    window.media.open_card(card("tv:7", "Severance", "tv"))
    pump(app)
    details_id = window.media_tabs.active_id
    details = window.media_tabs.page_for(details_id)
    _first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))

    first.player.set_fullscreen(True)
    pump(app)
    assert window.isFullScreen() is True
    assert window._header.isVisible() is False

    window.media_tabs.activate(details_id)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window.stack.currentWidget() is details
    # The application's own chrome is back, and the strip is still laid out.
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True
    assert window.media_tabs_bar.isVisible() is True
    # The player that was left has its bar back (its page is not the one in
    # front, so ``isVisible`` would be False for a reason unrelated to hiding).
    assert first.player.chrome.isHidden() is False
    assert first.player._fullscreen is False  # noqa: SLF001


def test_escape_from_a_paused_tab_restores_its_own_bar(qt_window, app):
    """B2: the bar a fullscreen session hid for a paused tab comes back."""
    window = qt_window
    first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    second_id, second = _playing_tab(window, app, card("tv:2", "Breaking Bad", "tv"))
    window.media_tabs.activate(first_id)
    pump(app)
    first.player.resume()
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    assert window.isFullScreen() is True

    # Switching to the paused tab keeps fullscreen: the strip is still usable.
    window.media_tabs.activate(second_id)
    pump(app)
    assert window.isFullScreen() is True
    assert second.player.pages.currentWidget() is second.player.paused
    assert second.player.chrome.isHidden() is True

    QTest.keyClick(second.player, Qt.Key_Escape)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window.stack.currentWidget() is second
    # The paused tab's bar was registered with the session, so it is visible.
    assert second.player.chrome.isVisible() is True
    assert first.player.chrome.isHidden() is False
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True


def test_resuming_a_paused_tab_after_fullscreen_keeps_its_bar_visible(qt_window, app):
    """B2: a paused tab resumed in normal window mode shows its bar."""
    window = qt_window
    first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    second_id, second = _playing_tab(window, app, card("tv:2", "Breaking Bad", "tv"))
    window.media_tabs.activate(first_id)
    pump(app)
    first.player.resume()
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    window.media_tabs.activate(second_id)
    pump(app)
    QTest.keyClick(second.player, Qt.Key_Escape)
    pump(app)
    assert window.isFullScreen() is False

    second.player.resume()
    pump(app)

    assert second.player.is_playing() is True
    assert window.isFullScreen() is False
    assert second.player.chrome.isVisible() is True
    assert second.player.pages.currentWidget() is second.player.view
    assert window._header.isVisible() is True


def test_leaving_media_from_fullscreen_restores_the_application_chrome(qt_window, app):
    window = qt_window
    first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    first.player.set_fullscreen(True)
    pump(app)
    assert window.isFullScreen() is True

    window.show_view("browse")
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True
    assert first.player.chrome.isHidden() is False


def test_closing_the_active_tab_in_fullscreen_leaves_no_stale_session(qt_window, app):
    """N1: closing a paused tab in front ends the fullscreen too."""
    window = qt_window
    first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    second_id, second = _playing_tab(window, app, card("tv:2", "Breaking Bad", "tv"))
    window.media_tabs.activate(first_id)
    pump(app)
    first.player.resume()
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    window.media_tabs.activate(second_id)
    pump(app)
    assert window.isFullScreen() is True

    window.media_tabs.close(second_id)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window.stack.currentWidget() is first
    assert first.player.chrome.isVisible() is True
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True
    assert window.media_tabs_bar.count() == 1


def _session(window):
    """The window's media fullscreen session, if it is in one."""
    return getattr(window, "_lumora_media_fullscreen", None)


def _close_and_free(window, app, tab_id, page):
    """Close a tab and run the deferred delete a real event loop would.

    Without that the page's widgets are still alive in the test process, and
    the crash a deleted player causes would never happen.
    """
    window.media_tabs.close(tab_id)
    pump(app)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pump(app)
    with pytest.raises(RuntimeError):
        page.player.chrome.show()
    return page


def _fullscreen_with_a_background_tab(window, app):
    """Play ``first`` fullscreen, switch to ``second`` and back again.

    The round trip is what registers the paused tab's player with the
    window's session while that tab is no longer the one in front.
    """
    first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    second_id, second = _playing_tab(window, app, card("tv:2", "Breaking Bad", "tv"))
    window.media_tabs.activate(first_id)
    pump(app)
    first.player.resume()
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    window.media_tabs.activate(second_id)
    pump(app)
    window.media_tabs.activate(first_id)
    pump(app)
    return first_id, first, second_id, second


def test_closing_a_background_tab_in_fullscreen_does_not_block_escape(qt_window, app):
    """B3: a deleted player must not stay in the fullscreen session.

    Closing the background tab left its player in ``session.players``; Escape
    then touched the deleted chrome widget, raised, and the window stayed
    fullscreen with the session already gone.
    """
    window = qt_window
    _first_id, first, second_id, second = _fullscreen_with_a_background_tab(
        window, app
    )
    session = _session(window)
    assert session is not None
    assert second.player in session.players

    _close_and_free(window, app, second_id, second)

    # The closed tab's player left the session with its page.
    session = _session(window)
    assert session is not None
    assert second.player not in session.players

    QTest.keyClick(first.player, Qt.Key_Escape)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window.isMaximized() is False
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True
    assert first.player.chrome.isHidden() is False


def test_closing_the_fullscreen_owners_tab_then_escape_exits_fullscreen(qt_window, app):
    """The tab that started the session can be closed while backgrounded."""
    window = qt_window
    first_id, first, second_id, second = _fullscreen_with_a_background_tab(
        window, app
    )

    # The tab in front keeps the session; the one that started it is closed
    # while it is behind the other one.
    window.media_tabs.activate(second_id)
    pump(app)
    _close_and_free(window, app, first_id, first)

    assert _session(window) is not None
    assert first.player not in _session(window).players

    QTest.keyClick(second.player, Qt.Key_Escape)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window.stack.currentWidget() is second
    assert second.player.chrome.isVisible() is True
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True


def test_closing_several_tabs_in_fullscreen_leaves_no_stale_players(qt_window, app):
    """Every closed tab's player goes; the session stays usable afterwards."""
    window = qt_window
    first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    second_id, second = _playing_tab(window, app, card("tv:2", "Breaking Bad", "tv"))
    third_id, third = _playing_tab(window, app, card("movie:4", "Heat"))
    window.media_tabs.activate(first_id)
    pump(app)
    first.player.resume()
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    # Every tab is brought in front once, so the session knows all players.
    window.media_tabs.activate(second_id)
    pump(app)
    window.media_tabs.activate(third_id)
    pump(app)
    window.media_tabs.activate(first_id)
    pump(app)
    assert in_window_fullscreen(window) is True
    assert _session(window).players == [
        first.player,
        second.player,
        third.player,
    ]

    _close_and_free(window, app, second_id, second)
    _close_and_free(window, app, third_id, third)

    session = _session(window)
    assert session is not None
    assert session.players == [first.player]

    QTest.keyClick(first.player, Qt.Key_Escape)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    # A fresh session starts cleanly from the player that is still open.
    first.player.resume()
    pump(app)
    first.player.set_fullscreen(True)
    pump(app)
    assert in_window_fullscreen(window) is True
    assert _session(window).players == [first.player]
    QTest.keyClick(first.player, Qt.Key_Escape)
    pump(app)
    assert in_window_fullscreen(window) is False


def test_leaving_media_after_closing_a_tab_in_fullscreen_restores_the_layout(
    qt_window, app
):
    window = qt_window
    _first_id, first, second_id, _second = _fullscreen_with_a_background_tab(
        window, app
    )
    _close_and_free(window, app, second_id, _second)

    window.show_view("browse")
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True
    assert first.player.chrome.isHidden() is False


@pytest.mark.parametrize("maximized", [False, True])
def test_escape_after_closing_a_tab_restores_the_window_state(
    qt_window, app, maximized
):
    """The state the window had before fullscreen comes back, not a default."""
    window = qt_window
    if maximized:
        window.showMaximized()
    else:
        window.showNormal()
    pump(app)
    _first_id, first, second_id, _second = _fullscreen_with_a_background_tab(
        window, app
    )
    assert window.isFullScreen() is True

    _close_and_free(window, app, second_id, _second)
    QTest.keyClick(first.player, Qt.Key_Escape)
    pump(app)

    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is False
    assert window.isMaximized() is maximized
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True


def test_a_deleted_player_never_keeps_the_window_fullscreen(qt_window, app):
    """Restoring the session survives a player whose widget is already gone."""
    window = qt_window
    _first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    first.player.set_fullscreen(True)
    pump(app)
    session = _session(window)
    assert session is not None

    class Gone:
        """What a deleted chrome widget answers with."""

        def show(self) -> None:
            raise RuntimeError("Internal C++ object already deleted")

    class DeletedPlayer:
        _fullscreen = False
        chrome = Gone()

    session.players.append(DeletedPlayer())

    assert end_window_fullscreen(window) is True
    pump(app)

    assert in_window_fullscreen(window) is False
    assert window.isFullScreen() is False
    assert window._header.isVisible() is True
    assert window._rail.isVisible() is True
    assert first.player.chrome.isHidden() is False


def test_a_failed_restore_still_gives_the_window_back(qt_window, app, monkeypatch):
    """Even an unexpected error must not strand the app in fullscreen."""
    window = qt_window
    _first_id, first = _playing_tab(window, app, card("movie:9", "Interstellar"))
    first.player.set_fullscreen(True)
    pump(app)
    session = _session(window)
    assert session is not None

    def broken_restore() -> None:
        raise RuntimeError("restore failed")

    monkeypatch.setattr(session, "restore", broken_restore)
    assert end_window_fullscreen(window) is True
    pump(app)

    # The window is out of fullscreen, and the session was not thrown away
    # with its state: a second attempt can still finish the exit.
    assert window.isFullScreen() is False
    assert in_window_fullscreen(window) is True
    monkeypatch.undo()
    assert end_window_fullscreen(window) is True
    pump(app)
    assert in_window_fullscreen(window) is False
    assert window.isFullScreen() is False
    assert window._header.isVisible() is True
    assert first.player.chrome.isHidden() is False
