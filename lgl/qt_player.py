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

from PySide6.QtCore import QEvent, QUrl, Qt, Signal
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
from .qt_widgets import label

#: Builds the widget that renders a streaming page; ``None`` means "no view".
ViewFactory = Callable[[QWidget], Any]


def webengine_view(parent: QWidget | None = None) -> Any:
    """A configured ``QWebEngineView``, or ``None`` when the build lacks it.

    WebEngine is an optional dependency: ``PySide6-Essentials`` has no
    ``QtWebEngineWidgets`` at all, so the import failing is the normal case for
    such a build and must not fall back to opening a browser.
    """
    try:
        from PySide6.QtWebEngineCore import QWebEngineSettings  # type: ignore
        from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore
    except Exception:  # noqa: BLE001 - WebEngine is optional in this build
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


class EmbeddedPlayer(QWidget):
    """The player as a page of the main window.

    ``play()`` shows the video, ``leave_page()`` is Back (stop, then ask the
    page to return), and ``set_fullscreen()`` hands the whole screen to the
    video and puts everything it hid back afterwards.
    """

    back_requested = Signal()
    fullscreen_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None, *,
                 view_factory: ViewFactory | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("playerPage")
        self._view_factory = view_factory or webengine_view
        self._url = ""
        self._fullscreen = False
        self._page_exit_requested = False
        self._hidden: list[tuple[QWidget, bool]] = []
        self._window_state: Qt.WindowState | None = None
        self._view_window: QWidget | None = None

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
        self.pages.setCurrentWidget(
            self.view if self.view is not None else self.fallback
        )

    # construction ---------------------------------------------------------
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
        self.title_label.setText(title or "Play")
        if self.view is not None:
            self.pages.setCurrentWidget(self.view)
            loader = getattr(self.view, "setUrl", None)
            if callable(loader):
                loader(QUrl(self._url))
        else:
            self.pages.setCurrentWidget(self.fallback)

    def stop_playback(self) -> None:
        """Stop loading and clear the video, like the GTK page's Back."""
        if self._fullscreen:
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
        """Back: stop the video and let the page that hosts it return."""
        if self._fullscreen:
            self.set_fullscreen(False)
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
            self._hide_around_video(window)
            self.chrome.hide()
            self._enter_window_fullscreen(window)
        else:
            self.chrome.show()
            self._restore_around_video()
            self._exit_window_fullscreen(window)
            if not page_started:
                # The app hid its own fullscreen while the page still believes
                # it is fullscreen, which breaks the provider's own button.
                self._tell_page_fullscreen_ended()
        self.fullscreen_changed.emit(active)

    def _hide_around_video(self, window: QWidget | None) -> None:
        """Hide every visible sibling from this page up to the window."""
        self._hidden = []
        child: QWidget = self
        parent = child.parentWidget()
        while parent is not None and parent is not window:
            for sibling in parent.children():
                if not isinstance(sibling, QWidget) or sibling is child:
                    continue
                if not sibling.isVisible():
                    continue
                self._hidden.append((sibling, True))
                sibling.hide()
            child = parent
            parent = child.parentWidget()

    def _restore_around_video(self) -> None:
        for widget, was_visible in reversed(self._hidden):
            widget.setVisible(was_visible)
        self._hidden = []

    def _enter_window_fullscreen(self, window: QWidget | None) -> None:
        if window is None:
            return
        self._window_state = window.windowState()
        window.showFullScreen()
        # QtWebEngine may move the view into its own fullscreen window; if it
        # did, that window has to cover the screen without a frame as well.
        self._cover_view_window(window)

    def _cover_view_window(self, window: QWidget) -> None:
        getter = getattr(self.view, "window", None)
        if not callable(getter):
            return
        try:
            view_window = getter()
        except Exception:  # noqa: BLE001
            return
        if view_window is None or view_window is window:
            return
        self._view_window = view_window
        view_window.setWindowFlag(Qt.FramelessWindowHint, True)
        view_window.showFullScreen()

    def _exit_window_fullscreen(self, window: QWidget | None) -> None:
        self._view_window = None
        if window is None:
            return
        state = self._window_state
        self._window_state = None
        if state is not None and state & Qt.WindowFullScreen:
            window.showFullScreen()
        elif state is not None and state & Qt.WindowMaximized:
            window.showMaximized()
        else:
            window.showNormal()

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


__all__ = ["EmbeddedPlayer", "ViewFactory", "make_player", "webengine_view"]
