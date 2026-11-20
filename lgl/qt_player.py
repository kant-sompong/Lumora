"""Embedded playback page for the Qt (Windows) frontend.

The GTK build plays inside its Movies page (``movies_ui.py``): no second window,
a Back action and a fullscreen mode that hands the whole screen to the video.
This module gives the Qt build the same behaviour with one reusable page.

``EmbeddedPlayer`` is built through :func:`make_player`, so a test (or a build
without Qt WebEngine) can supply its own view.  Without WebEngine the page shows
a panel with an "Open in browser" button instead of opening the browser by
itself.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import platform_support
from .media_tabs import format_position, with_start
from .qt_widgets import label

# Qt refuses to load WebEngine once a QCoreApplication exists ("QtWebEngineWidgets
# must be imported before a QCoreApplication instance is created"), and every
# window is built after the QApplication, so the import has to happen here, at
# module import, and not when the first video starts. Doing it late is exactly
# how the Windows build ended up with no embedded player: the import raised, the
# page fell back to its "open in browser" panel, and nothing said why.
try:  # noqa: SIM105 - the failure is the flag
    from PySide6.QtWebEngineCore import QWebEngineSettings  # type: ignore
    from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

    #: False in a build that ships PySide6-Essentials, where there is no WebEngine.
    WEBENGINE_AVAILABLE = True
except Exception:  # noqa: BLE001 - WebEngine is optional in this build
    QWebEngineSettings = None  # type: ignore[assignment]
    QWebEngineView = None  # type: ignore[assignment]
    WEBENGINE_AVAILABLE = False

def preload_webengine() -> bool:
    """True when this build has WebEngine; importing this module already did it.

    Called before the QApplication is created, purely so the intent is visible
    at the call site: after that point Qt will not load WebEngine at all.
    """
    return WEBENGINE_AVAILABLE


#: Builds the widget that renders a streaming page; ``None`` means "no view".
ViewFactory = Callable[[QWidget], Any]


def webengine_view(parent: QWidget | None = None) -> Any:
    """A configured ``QWebEngineView``, or ``None`` when the build lacks it.

    WebEngine is an optional dependency: ``PySide6-Essentials`` has no
    ``QtWebEngineWidgets`` at all, so the import failing is the normal case for
    such a build and must not fall back to opening a browser.
    """
    if QWebEngineView is None or QWebEngineSettings is None:
        return None
    from PySide6.QtGui import QColor

    try:
        view = QWebEngineView(parent)
        page = view.page()
        # The provider pages ask for fullscreen themselves; let them, and keep
        # autoplay working the way the GTK player does.
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )
        page.setBackgroundColor(QColor("#000000"))
    except Exception:  # noqa: BLE001 - a broken WebEngine is no player at all
        return None
    return view


#: Where a window keeps the media fullscreen it is in, so a tab switch can
#: keep it.  The video is not the owner: the window is.
_FULLSCREEN_ATTR = "_lumora_media_fullscreen"


def _window_session(window: QWidget | None) -> "_FullscreenSession | None":
    if window is None:
        return None
    return getattr(window, _FULLSCREEN_ATTR, None)


def in_window_fullscreen(window: QWidget | None) -> bool:
    """True while this window is in media fullscreen.

    Read by the tab controller: whether a tab switch keeps the window
    fullscreen depends on the window's own session, not on the tab's player.
    """
    return _window_session(window) is not None


class _FullscreenSession:
    """One window's media fullscreen: chrome hidden, video on the whole screen.

    Fullscreen belongs to the window, not to a single player.  Switching tabs
    therefore keeps it, and the media tab strip is left alone: it stays at the
    top of the screen so another open title can be clicked without leaving
    fullscreen.  Whichever player ends the session puts everything back.
    """

    def __init__(self, window: QWidget) -> None:
        self.window = window
        #: Widgets hidden for this session, with the visibility to restore.
        self.hidden: list[tuple[QWidget, bool]] = []
        #: Players whose own bar (Back, title) this session hid.
        self.players: list[QWidget] = []
        self.window_state = window.windowState()
        self.view_window: QWidget | None = None

    def hide_around(self, player: QWidget) -> None:
        """Hide every visible sibling from ``player`` up to the window.

        A widget that asks to stay (the media tab strip) is skipped: hiding it
        is what made the tabs unreachable during fullscreen.
        """
        child: QWidget = player
        parent = child.parentWidget()
        while parent is not None and parent is not self.window:
            for sibling in parent.children():
                if not isinstance(sibling, QWidget) or sibling is child:
                    continue
                if getattr(sibling, "stays_during_fullscreen", False):
                    continue
                if not sibling.isVisible():
                    continue
                self.hidden.append((sibling, True))
                sibling.hide()
            child = parent
            parent = child.parentWidget()

    def restore(self) -> None:
        for widget, was_visible in reversed(self.hidden):
            widget.setVisible(was_visible)
        self.hidden = []
        for player in self.players:
            player._fullscreen = False
            player.chrome.show()
        self.players = []
        window = self.window
        state = self.window_state
        self.window_state = None
        if state is not None and state & Qt.WindowFullScreen:
            window.showFullScreen()
        elif state is not None and state & Qt.WindowMaximized:
            window.showMaximized()
        else:
            window.showNormal()


def start_window_fullscreen(player: QWidget) -> "_FullscreenSession | None":
    """Put ``player``'s window in media fullscreen, once per window."""
    window = player.window()
    if window is None:
        return None
    session = _window_session(window)
    if session is None:
        session = _FullscreenSession(window)
        try:
            setattr(window, _FULLSCREEN_ATTR, session)
        except Exception:  # noqa: BLE001 - a window that cannot carry state
            return None
        session.hide_around(player)
        window.showFullScreen()
    if player not in session.players:
        session.players.append(player)
    # QtWebEngine may move the view into its own fullscreen window; if it did,
    # that window has to cover the screen without a frame as well.
    _cover_view_window(session, player)
    return session


def end_window_fullscreen(window: QWidget | None) -> bool:
    """Leave media fullscreen if this window is in it; True when it was."""
    session = _window_session(window)
    if session is None:
        return False
    try:
        delattr(window, _FULLSCREEN_ATTR)
    except Exception:  # noqa: BLE001 - already gone
        pass
    session.restore()
    return True


def _cover_view_window(session: _FullscreenSession, player: QWidget) -> None:
    getter = getattr(player.view, "window", None) if player.view is not None else None
    if not callable(getter):
        return
    try:
        view_window = getter()
    except Exception:  # noqa: BLE001
        return
    if view_window is None or view_window is session.window:
        return
    session.view_window = view_window
    view_window.setWindowFlag(Qt.FramelessWindowHint, True)
    view_window.showFullScreen()


class EmbeddedPlayer(QWidget):
    """The player as a page of the main window.

    ``play()`` shows the video, ``leave_page()`` is Back (stop, then ask the
    page to return), and ``set_fullscreen()`` hands the whole screen to the
    video and puts everything it hid back afterwards.
    """

    back_requested = Signal()
    next_requested = Signal()
    fullscreen_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None, *,
                 view_factory: ViewFactory | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("playerPage")
        self._view_factory = view_factory or webengine_view
        self._url = ""
        #: Where the video is, as last read from the page (seconds).
        self._position = 0.0
        #: Bumped whenever the view is freed: a late JavaScript answer that
        #: belongs to the old view must not overwrite a newer position.
        self._view_token = 0
        #: ``(url, title, position)`` kept while this tab is in the background.
        self._resume: tuple[str, str, float] = ("", "", 0.0)
        self._sample_timer = QTimer(self)
        self._sample_timer.setInterval(self.POSITION_SAMPLE_MS)
        self._sample_timer.timeout.connect(self._request_position)
        self._fullscreen = False
        self._page_exit_requested = False

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.chrome = QFrame()
        self.chrome.setObjectName("playerBar")
        bar = QHBoxLayout(self.chrome)
        bar.setContentsMargins(16, 8, 16, 8)
        bar.setSpacing(10)
        self.back = QPushButton("‹  Back")
        self.back.setObjectName("heroAction")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.setToolTip("Stop playback and go back")
        self.back.clicked.connect(self.leave_page)
        # Escape inside the web view never reaches this page's keyPressEvent, so
        # a widget-scoped shortcut catches it wherever the focus is.
        self._escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._escape.setContext(Qt.WidgetWithChildrenShortcut)
        self._escape.activated.connect(self.handle_escape)
        bar.addWidget(self.back)
        self.title_label = label("", "playerTitle")
        bar.addWidget(self.title_label, 1)
        #: Shown only while an episode plays and the next one is known.
        self.next_episode = QPushButton("Next episode  ›")
        self.next_episode.setObjectName("heroAction")
        self.next_episode.setCursor(Qt.PointingHandCursor)
        self.next_episode.setToolTip("Play the next episode")
        self.next_episode.setVisible(False)
        self.next_episode.clicked.connect(lambda _checked=False: self.next_requested.emit())
        bar.addWidget(self.next_episode)
        column.addWidget(self.chrome)

        self.stage = QFrame()
        self.stage.setObjectName("playerStage")
        stage = QVBoxLayout(self.stage)
        stage.setContentsMargins(0, 0, 0, 0)
        stage.setSpacing(0)
        self.pages = QStackedWidget()
        stage.addWidget(self.pages)
        column.addWidget(self.stage, 1)

        self.view: Any = None
        try:
            self.view = self._view_factory(self.pages)
        except Exception:  # noqa: BLE001 - a factory that fails means no player
            self.view = None
        if self.view is not None:
            self.pages.addWidget(self.view)
            self._connect_view(self.view)
        self.fallback = self._build_fallback()
        self.pages.addWidget(self.fallback)
        self.paused = self._build_paused()
        self.pages.addWidget(self.paused)
        self.pages.setCurrentWidget(
            self.view if self.view is not None else self.fallback
        )

    #: How often the open player asks the page where the video is.
    POSITION_SAMPLE_MS = 4000

    # construction ---------------------------------------------------------
    def _build_paused(self) -> QWidget:
        """What a backgrounded tab shows: where it stopped and one way back.

        Nothing is loaded here — returning to a tab must not start video by
        itself — so the view can stay released until Resume is pressed.
        """
        panel = QWidget()
        panel.setObjectName("playerPaused")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(28, 28, 28, 28)
        inner.setSpacing(10)
        inner.addStretch(1)
        inner.addWidget(label("Playback paused", "pageTitle"))
        self.paused_note = label(
            "This tab was playing when you switched away.",
            "detailNote", wrap=True,
        )
        inner.addWidget(self.paused_note)
        self.resume_button = QPushButton("▶  Resume")
        self.resume_button.setObjectName("suggested")
        self.resume_button.setCursor(Qt.PointingHandCursor)
        self.resume_button.setToolTip("Continue this tab from where it stopped")
        self.resume_button.clicked.connect(self.resume)
        inner.addWidget(self.resume_button, 0, Qt.AlignLeft)
        self.paused_browser = QPushButton("Open in browser")
        self.paused_browser.setObjectName("heroAction")
        self.paused_browser.setCursor(Qt.PointingHandCursor)
        self.paused_browser.clicked.connect(self.open_in_browser)
        inner.addWidget(self.paused_browser, 0, Qt.AlignLeft)
        inner.addStretch(1)
        return panel

    def _build_fallback(self) -> QWidget:
        """What a build without WebEngine shows: a button, never a pop-up."""
        panel = QWidget()
        panel.setObjectName("playerFallback")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(28, 28, 28, 28)
        inner.setSpacing(10)
        inner.addStretch(1)
        inner.addWidget(label("Playback opens in your browser", "pageTitle"))
        self.fallback_note = label(
            "This build has no embedded player, so the provider page is only "
            "opened when you press the button below. Nothing is downloaded.",
            "detailNote", wrap=True,
        )
        inner.addWidget(self.fallback_note)
        self.open_browser = QPushButton("Open in browser")
        self.open_browser.setObjectName("suggested")
        self.open_browser.setCursor(Qt.PointingHandCursor)
        self.open_browser.clicked.connect(self.open_in_browser)
        inner.addWidget(self.open_browser, 0, Qt.AlignLeft)
        inner.addStretch(1)
        return panel

    def _connect_view(self, view: Any) -> None:
        """Listen for the page's fullscreen request and for Escape in the view."""
        try:
            page = view.page()
        except Exception:  # noqa: BLE001 - a stub view may have no page()
            page = None
        signal = getattr(page, "fullScreenRequested", None) if page else None
        if signal is not None:
            try:
                signal.connect(self._on_fullscreen_request)
            except Exception:  # noqa: BLE001 - not a usable signal
                pass
        view.installEventFilter(self)

    # playback -------------------------------------------------------------
    def play(self, url: str, title: str = "") -> None:
        """Show ``url`` in the page; no other window is opened."""
        self._url = str(url or "")
        self._position = 0.0
        self._resume = ("", "", 0.0)
        self.title_label.setText(title or "Play")
        self._ensure_view()
        if self.view is not None:
            self.pages.setCurrentWidget(self.view)
            loader = getattr(self.view, "setUrl", None)
            if callable(loader):
                loader(QUrl(self._url))
        else:
            self.pages.setCurrentWidget(self.fallback)
        # Resuming a tab inside a fullscreen window stays fullscreen: the tab
        # strip is still above the video and Esc still leaves fullscreen first.
        if _window_session(self.window()) is not None and not self._fullscreen:
            self._fullscreen = True
            self.chrome.hide()
            session = _window_session(self.window())
            if session is not None and self not in session.players:
                session.players.append(self)
        if self._url:
            self._sample_timer.start()

    def play_at(self, url: str, title: str = "", position: object = 0.0) -> None:
        """Play ``url`` starting at ``position``, the way a tab resumes."""
        try:
            seconds = max(0.0, float(position or 0.0))
        except (TypeError, ValueError):
            seconds = 0.0
        self.play(with_start(url, seconds), title)
        self._position = seconds

    # tab lifecycle ---------------------------------------------------------
    def is_playing(self) -> bool:
        """True while a URL is loaded in a live view."""
        return bool(self._url) and self.view is not None and (
            self.pages.currentWidget() is self.view
        )

    def current_url(self) -> str:
        """The URL on screen, or the one a paused tab will resume."""
        return self._url or self._resume[0]

    def saved_position(self) -> float:
        """Where a tab with no running view would resume from."""
        if self._url:
            return float(self._position or 0.0)
        return float(self._resume[2] or 0.0)

    def capture_position(self) -> float:
        """Ask the page where the video is, and answer with the last sample.

        Reading ``currentTime`` is asynchronous, so the sampler keeps a recent
        value and a fresh request is queued for the next switch.
        """
        self._request_position()
        return float(self._position or 0.0)

    def suspend(self, keep_fullscreen: bool = False) -> float:
        """Background this tab: save the position, stop, free the view.

        ``keep_fullscreen`` is the tab-switch case: the window stays fullscreen
        for whichever tab comes in front, so switching never drops out of it.
        """
        url, title = self._url, self.title_label.text()
        position = self.capture_position() if url else 0.0
        if url:
            self._resume = (url, title, position)
        self.stop_playback(keep_fullscreen=keep_fullscreen)
        self.release_resources(keep_fullscreen=keep_fullscreen)
        if keep_fullscreen:
            # The page is leaving the screen; the session keeps the window
            # fullscreen and the bar this player hid stays hidden until then.
            self._fullscreen = False
        if url:
            where = format_position(position)
            self.paused_note.setText(
                f"Resume {title or 'this title'}" + (f" from {where}" if where else "")
            )
            self.pages.setCurrentWidget(self.paused)
        return position

    def pause_for_tab(self, url: str, title: str = "", position: object = 0.0) -> None:
        """Show the paused panel for a tab restored from its saved state."""
        try:
            seconds = max(0.0, float(position or 0.0))
        except (TypeError, ValueError):
            seconds = 0.0
        self._url = ""
        self._resume = (str(url or ""), str(title or ""), seconds)
        self.title_label.setText(title or "")
        where = format_position(seconds)
        self.paused_note.setText(
            f"Resume {title or 'this title'}" + (f" from {where}" if where else "")
        )
        self.pages.setCurrentWidget(self.paused)
        # A tab restored inside a fullscreen window keeps the same screen: the
        # strip and the paused panel stay, and this player's own bar does not
        # reappear until fullscreen is left.  The bar this hid has to be the
        # session's to put back, or leaving fullscreen would keep it hidden.
        session = _window_session(self.window())
        if session is not None:
            self.chrome.hide()
            if self not in session.players:
                session.players.append(self)

    def resume(self) -> None:
        """Continue the backgrounded tab at its saved position."""
        url, title, position = self._resume
        if not url:
            return
        self.play_at(url, title, position)

    def release_resources(self, keep_fullscreen: bool = False) -> None:
        """Free the web view of a backgrounded tab; it is rebuilt on resume."""
        if self._fullscreen and not keep_fullscreen:
            self.set_fullscreen(False)
        self._sample_timer.stop()
        view = self.view
        self.view = None
        self._view_token += 1
        if view is None:
            return
        stopper = getattr(view, "stop", None)
        if callable(stopper):
            try:
                stopper()
            except Exception:  # noqa: BLE001 - a view that is already gone
                pass
        self.pages.removeWidget(view)
        deleter = getattr(view, "deleteLater", None)
        if callable(deleter):
            try:
                deleter()
            except Exception:  # noqa: BLE001
                pass

    def _ensure_view(self) -> bool:
        """Build the view again after :meth:`release_resources` freed it."""
        if self.view is not None:
            return True
        try:
            view = self._view_factory(self.pages)
        except Exception:  # noqa: BLE001 - a factory that fails means no player
            view = None
        if view is None:
            return False
        self.view = view
        self.pages.addWidget(view)
        self._connect_view(view)
        return True

    def _request_position(self) -> None:
        """Ask the page for ``currentTime``; a stub view may not answer."""
        view = self.view
        getter = getattr(view, "page", None) if view is not None else None
        try:
            page = getter() if callable(getter) else getter
        except Exception:  # noqa: BLE001 - a view that has no page yet
            page = None
        runner = getattr(page, "runJavaScript", None) if page is not None else None
        if not callable(runner):
            return
        script = (
            "(() => { const v = document.querySelector('video');"
            " return v && isFinite(v.currentTime) ? v.currentTime : 0; })()"
        )
        token = self._view_token

        def store(value: object) -> None:
            # The view may have been released while the page was answering.
            if token != self._view_token or self.view is None:
                return
            self._store_position(value)

        try:
            runner(script, store)
        except Exception:  # noqa: BLE001 - a page that is still loading
            pass

    def _store_position(self, value: object) -> None:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return
        if seconds != seconds or seconds < 0:  # NaN or nonsense
            return
        self._position = seconds

    def offer_next_episode(self, tooltip: str = "") -> None:
        """Show the button that plays the episode after the one on screen."""
        self.next_episode.setToolTip(tooltip or "Play the next episode")
        self.next_episode.setVisible(True)

    def clear_next_episode(self) -> None:
        """Hide it: a movie, or the last episode of a show, has no next one."""
        self.next_episode.setVisible(False)

    def stop_playback(self, keep_fullscreen: bool = False) -> None:
        """Stop loading and clear the video, like the GTK page's Back."""
        self.clear_next_episode()
        self._sample_timer.stop()
        if self._fullscreen and not keep_fullscreen:
            self.set_fullscreen(False)
        view = self.view
        if view is not None:
            stopper = getattr(view, "stop", None)
            if callable(stopper):
                try:
                    stopper()
                except Exception:  # noqa: BLE001
                    pass
            loader = getattr(view, "setUrl", None)
            if callable(loader):
                loader(QUrl("about:blank"))
        self._url = ""
        self.title_label.setText("")

    def leave_page(self) -> None:
        """Back: leave fullscreen first, then stop and return to the page.

        A paused tab shown inside a fullscreen window has no ``_fullscreen`` of
        its own, so the window's session is ended here too: Escape always gets
        the normal layout back before anything navigates.
        """
        if self._fullscreen:
            self.set_fullscreen(False)
            return
        if end_window_fullscreen(self.window()):
            return
        self.stop_playback()
        self.back_requested.emit()

    def open_in_browser(self) -> None:
        if self._url:
            platform_support.open_url(self._url)

    def handle_escape(self) -> None:
        """Escape leaves fullscreen first, then goes back."""
        self.leave_page()

    # escaping the video ---------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.key() == Qt.Key_Escape:
            self.handle_escape()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt's name
        """Escape pressed inside the web view comes back here."""
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key_Escape:
            self.handle_escape()
            return True
        return super().eventFilter(watched, event)

    def _on_fullscreen_request(self, request: Any) -> None:
        """The provider page asked for fullscreen: accept it and make it real."""
        try:
            request.accept()
        except Exception:  # noqa: BLE001 - a stub request
            pass
        active = True
        toggler = getattr(request, "toggleOn", None)
        if callable(toggler):
            try:
                active = bool(toggler())
            except Exception:  # noqa: BLE001
                active = True
        # The page already knows it left fullscreen; telling it again would
        # toggle its own fullscreen button state back.
        self._page_exit_requested = not active
        self.set_fullscreen(active)

    def set_fullscreen(self, active: bool) -> None:
        """Give the video the whole screen, hiding the app around it."""
        active = bool(active)
        if active == self._fullscreen:
            self._page_exit_requested = False
            return
        self._fullscreen = active
        page_started = (not active) and self._page_exit_requested
        self._page_exit_requested = False
        window = self.window()
        if active:
            self.chrome.hide()
            start_window_fullscreen(self)
            self._fullscreen = True
        else:
            session = _window_session(window)
            if session is not None and self in session.players:
                end_window_fullscreen(window)
            else:
                self.chrome.show()
            self._fullscreen = False
            if not page_started:
                # The app hid its own fullscreen while the page still believes
                # it is fullscreen, which breaks the provider's own button.
                self._tell_page_fullscreen_ended()
        self.fullscreen_changed.emit(active)

    def _tell_page_fullscreen_ended(self) -> None:
        """Ask the page to leave fullscreen when the app started the exit.

        The import is local on purpose: a build with PySide6-Essentials has no
        QtWebEngineCore at all, and this has to be a silent no-op there.
        """
        getter = getattr(self.view, "page", None)
        if not callable(getter):
            return
        try:
            page = getter()
        except Exception:  # noqa: BLE001 - a view that has no page yet
            return
        trigger = getattr(page, "triggerAction", None)
        if not callable(trigger):
            return
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage  # type: ignore
        except Exception:  # noqa: BLE001 - WebEngine is optional in this build
            return
        try:
            trigger(QWebEnginePage.WebAction.ExitFullScreen)
        except Exception:  # noqa: BLE001 - the page is gone or still loading
            pass


def make_player(parent: QWidget | None = None, *,
                view_factory: ViewFactory | None = None) -> EmbeddedPlayer:
    """The small factory the media page (and tests) build the player through."""
    return EmbeddedPlayer(parent, view_factory=view_factory)


__all__ = [
    "EmbeddedPlayer",
    "format_position",
    "with_start",
    "ViewFactory",
    "WEBENGINE_AVAILABLE",
    "make_player",
    "preload_webengine",
    "webengine_view",
]
