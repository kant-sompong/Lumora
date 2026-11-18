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

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget  # noqa: E402

from lgl import qt_media, qt_media_tabs  # noqa: E402
from lgl.qt_player import EmbeddedPlayer  # noqa: E402
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
