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
