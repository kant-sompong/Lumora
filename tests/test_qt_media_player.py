"""The Qt player plays inside the Media page, not in a second window.

Everything here runs offscreen and uses a stub view, so no Qt WebEngine and no
network are needed.  Each test builds the real ``MediaPage`` inside a window
shaped like the app (rail, header, page stack).
"""

from __future__ import annotations

import os
import sys
import time
import types

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lgl import qt_media, qt_player  # noqa: E402

CARD = qt_media.Card(
    key="movie:603", title="The Matrix", year="1999", kind="movie", tmdb_id=603
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


class StubPage(QObject):
    """What the player uses from QWebEnginePage: requests and actions."""

    fullScreenRequested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.actions: list[object] = []

    def triggerAction(self, action) -> None:  # noqa: N802 - Qt's name
        self.actions.append(action)


class StubView(QWidget):
    """A stand-in for QWebEngineView: records loads instead of networking."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = StubPage()
        self.loaded: list[str] = []
        self.stopped = False

    def page(self) -> StubPage:
        return self._page

    def setUrl(self, url) -> None:  # noqa: N802 - Qt's name
        text = url.toString() if isinstance(url, QUrl) else str(url)
        self.loaded.append(text)

    def stop(self) -> None:
        self.stopped = True


class Request:
    """A stand-in for QWebEngineFullScreenRequest."""

    def __init__(self, on: bool) -> None:
        self.on = on
        self.accepted = False

    def accept(self) -> None:
        self.accepted = True

    def toggleOn(self) -> bool:
        return self.on


class Harness:
    """The real Media page inside a window laid out like the app."""

    def __init__(self, app: QApplication, *, stub: bool = True) -> None:
        self.window = QMainWindow()
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        self.rail = QFrame()
        self.rail.setFixedWidth(64)
        shell.addWidget(self.rail)
        self.body = QWidget()
        column = QVBoxLayout(self.body)
        column.setContentsMargins(0, 0, 0, 0)
        self.header = QFrame()
        self.header.setFixedHeight(56)
        column.addWidget(self.header)
        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)
        shell.addWidget(self.body, 1)
        self.window.setCentralWidget(root)

        if stub:
            self.page = qt_media.MediaPage(player_factory=self._stub_player)
        else:
            self.page = qt_media.MediaPage()  # uses the real factory
        self.stack.addWidget(self.page)
        self.window.resize(1200, 780)
        self.window.show()
        pump(app)

    @staticmethod
    def _stub_player(parent: QWidget) -> qt_player.EmbeddedPlayer:
        return qt_player.EmbeddedPlayer(parent, view_factory=StubView)


def test_watch_creates_no_new_top_level_widget(app):
    harness = Harness(app)
    before = set(QApplication.topLevelWidgets())
    harness.page._play_tmdb(CARD)
    pump(app)
    after = set(QApplication.topLevelWidgets())

    opened = after - before
    assert opened == set(), "Watch opened a window of its own"
    assert not any(isinstance(widget, QDialog) for widget in opened)
    assert harness.page.stack.currentWidget() is harness.page.player
    assert harness.page.player.view.loaded, "the stream was never loaded"


def test_the_player_page_is_current_and_back_restores_the_previous_page(app):
    harness = Harness(app)
    harness.page.show_section("movies")
    scroll = harness.page._pages["movies"]
    assert isinstance(scroll, QScrollArea)
    filler = QWidget()
    filler.setFixedHeight(4000)
    scroll.widget().layout().addWidget(filler)
    pump(app)
    bar = scroll.verticalScrollBar()
    bar.setValue(150)
    assert bar.value() == 150

    harness.page._play_tmdb(CARD)
    pump(app)
    assert harness.page.stack.currentWidget() is harness.page.player

    harness.page.player.back.click()
    pump(app)
    assert harness.page.stack.currentWidget() is scroll
    assert bar.value() == 150, "the previous page lost its scroll position"
    assert harness.page.player.title_label.text() == ""
    assert harness.page.player.view.stopped is True


def test_escape_leaves_the_player_when_not_fullscreen(app):
    harness = Harness(app)
    harness.page._play_tmdb(CARD)
    pump(app)
    QTest.keyClick(harness.page.player, Qt.Key_Escape)
    pump(app)
    assert harness.page.stack.currentWidget() is harness.page._pages["home"]


def _siblings_on_the_way_up(player: QWidget, window: QWidget) -> list[QWidget]:
    found: list[QWidget] = []
    child: QWidget = player
    parent = child.parentWidget()
    while parent is not None and parent is not window:
        for sibling in parent.children():
            if isinstance(sibling, QWidget) and sibling is not child:
                found.append(sibling)
        child = parent
        parent = child.parentWidget()
    return found


def test_fullscreen_hides_every_sibling_and_leaving_restores_it(app):
    harness = Harness(app)
    harness.page.show_section("movies")
    pump(app)
    media_bar = harness.page.layout().itemAt(0).widget()
    harness.page._play_tmdb(CARD)
    pump(app)
    visible_before = [
        widget for widget in _siblings_on_the_way_up(harness.page.player, harness.window)
        if widget.isVisible()
    ]
    assert harness.rail in visible_before
    assert harness.header in visible_before
    assert media_bar in visible_before

    harness.page.player.set_fullscreen(True)
    pump(app)
    assert all(widget.isHidden() for widget in visible_before)
    assert harness.page.player.chrome.isHidden()
    assert harness.window.isFullScreen()

    # Escape leaves fullscreen first, and does not leave the player.
    QTest.keyClick(harness.page.player, Qt.Key_Escape)
    pump(app)
    assert not harness.window.isFullScreen()
    assert harness.page.stack.currentWidget() is harness.page.player
    assert all(widget.isVisible() for widget in visible_before)
    assert not harness.page.player.chrome.isHidden()


def test_a_maximised_window_is_maximised_again_afterwards(app):
    harness = Harness(app)
    harness.window.showMaximized()
    pump(app)
    assert harness.window.isMaximized()
    harness.page._play_tmdb(CARD)
    harness.page.player.set_fullscreen(True)
    pump(app)
    assert harness.window.isFullScreen()

    harness.page.player.set_fullscreen(False)
    pump(app)
    assert not harness.window.isFullScreen()
    assert harness.window.isMaximized()


def test_a_provider_fullscreen_request_is_accepted(app):
    harness = Harness(app)
    harness.page._play_tmdb(CARD)
    pump(app)
    entering = Request(True)
    harness.page.player.view.page().fullScreenRequested.emit(entering)
    pump(app)
    assert entering.accepted is True
    assert harness.window.isFullScreen()

    leaving = Request(False)
    harness.page.player.view.page().fullScreenRequested.emit(leaving)
    pump(app)
    assert leaving.accepted is True
    assert not harness.window.isFullScreen()


def test_without_webengine_the_browser_is_not_opened_automatically(app, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(qt_player, "webengine_view", lambda parent=None: None)
    monkeypatch.setattr(qt_player.platform_support, "open_url", opened.append)
    harness = Harness(app, stub=False)

    harness.page._play_tmdb(CARD)
    pump(app)

    assert harness.page.player.view is None
    assert harness.page.player.pages.currentWidget() is harness.page.player.fallback
    assert opened == [], "Watch opened the browser by itself"
    assert harness.page.player.fallback_note.text()

    harness.page.player.open_browser.click()
    assert len(opened) == 1 and "603" in opened[0]


class _WebAction:
    ExitFullScreen = "ExitFullScreen"


class _FakeQWebEnginePage:
    WebAction = _WebAction


def _fake_webengine(monkeypatch) -> None:
    """Stand in for QtWebEngineCore, which this build does not ship."""
    module = types.ModuleType("PySide6.QtWebEngineCore")
    module.QWebEnginePage = _FakeQWebEnginePage
    monkeypatch.setitem(sys.modules, "PySide6.QtWebEngineCore", module)


def test_escape_from_fullscreen_asks_the_page_to_exit(app, monkeypatch):
    _fake_webengine(monkeypatch)
    harness = Harness(app)
    harness.page._play_tmdb(CARD)
    pump(app)
    page = harness.page.player.view.page()
    harness.page.player.set_fullscreen(True)
    pump(app)
    assert page.actions == []

    QTest.keyClick(harness.page.player, Qt.Key_Escape)
    pump(app)

    assert not harness.window.isFullScreen()
    assert page.actions == [_WebAction.ExitFullScreen]


def test_a_page_initiated_exit_does_not_ask_the_page_again(app, monkeypatch):
    _fake_webengine(monkeypatch)
    harness = Harness(app)
    harness.page._play_tmdb(CARD)
    pump(app)
    page = harness.page.player.view.page()
    harness.page.player.set_fullscreen(True)

    leaving = Request(False)
    page.fullScreenRequested.emit(leaving)
    pump(app)

    assert leaving.accepted is True
    assert not harness.window.isFullScreen()
    assert page.actions == [], "a page-started exit was answered twice"


def test_leaving_fullscreen_without_a_page_or_webengine_does_not_raise(app, monkeypatch):
    # A view whose page() does not exist: there is nothing to tell.
    window = QMainWindow()
    bare = qt_player.EmbeddedPlayer(window, view_factory=lambda parent: QWidget(parent))
    window.setCentralWidget(bare)
    window.show()
    pump(app)
    bare.set_fullscreen(True)
    bare.set_fullscreen(False)
    pump(app)
    assert not window.isFullScreen()
    window.close()

    # A build without QtWebEngineCore: a page that has triggerAction must not
    # be called, and leaving fullscreen must still work.
    monkeypatch.setitem(sys.modules, "PySide6.QtWebEngineCore", None)
    harness = Harness(app)
    harness.page._play_tmdb(CARD)
    pump(app)
    page = harness.page.player.view.page()
    harness.page.player.set_fullscreen(True)
    harness.page.player.set_fullscreen(False)
    pump(app)
    assert page.actions == []
    assert not harness.window.isFullScreen()


def test_webengine_is_loaded_when_the_module_imports():
    """Qt refuses to load WebEngine once a QCoreApplication exists.

    The player is built after the QApplication, so the import has to happen at
    module import or every build silently falls back to "open in browser".
    """
    import importlib

    class Settings:
        class WebAttribute:
            FullScreenSupportEnabled = 1
            PlaybackRequiresUserGesture = 2

        def setAttribute(self, *_args):
            pass

    class Page:
        def settings(self):
            return Settings()

        def setBackgroundColor(self, _colour):
            pass

    class View(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._page = Page()
            self.url = ""

        def page(self):
            return self._page

        def setUrl(self, url):
            self.url = str(url)

    core = types.ModuleType("PySide6.QtWebEngineCore")
    core.QWebEngineSettings = Settings
    widgets = types.ModuleType("PySide6.QtWebEngineWidgets")
    widgets.QWebEngineView = View

    names = ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets")
    saved = {name: sys.modules.get(name) for name in names}
    try:
        sys.modules["PySide6.QtWebEngineCore"] = core
        sys.modules["PySide6.QtWebEngineWidgets"] = widgets
        reloaded = importlib.reload(qt_player)
        assert reloaded.WEBENGINE_AVAILABLE is True
        view = reloaded.webengine_view()
        assert isinstance(view, View), "the preloaded view class must be used"
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        importlib.reload(qt_player)
