"""Browser-style media tabs for the Qt (Windows) frontend.

The strip under the header is the window's own tab bar: it stays visible while
Browse, Library and Downloads are open, and each tab is a real media session —
its own detail page, episode selection, scroll position and player — not a
button that reopens a title from scratch.

Only the active tab may keep a video view alive.  Switching away suspends that
tab's player (position saved, WebEngine view released) and returning restores
the page and the saved position, paused until Resume is pressed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import media_tabs
from .qt_media import MediaPage
from .qt_player import end_window_fullscreen, in_window_fullscreen
from .qt_widgets import label

#: Long titles are shortened here so one tab cannot squeeze out the others.
TITLE_LIMIT = 26


def _elide(text: str, limit: int = TITLE_LIMIT) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class MediaTabButton(QFrame):
    """One tab: title, small grey subtitle, and its own close button."""

    activated = Signal(str)
    closed = Signal(str)

    def __init__(self, tab: media_tabs.TabState, active: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mediaTab")
        self._tab_id = tab.tab_id
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{tab.title}\n{tab.subtitle}")
        row = QHBoxLayout(self)
        row.setContentsMargins(11, 4, 4, 4)
        row.setSpacing(6)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self.title_label = label(_elide(tab.title), "mediaTabTitle")
        self.meta_label = label(tab.subtitle, "mediaTabMeta")
        text.addWidget(self.title_label)
        text.addWidget(self.meta_label)
        row.addLayout(text, 1)

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("mediaTabClose")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFixedSize(18, 18)
        self.close_button.setToolTip(f"Close {tab.title}")
        self.close_button.clicked.connect(lambda _=False: self.closed.emit(self._tab_id))
        row.addWidget(self.close_button, 0, Qt.AlignVCenter)

        self.setProperty("active", bool(active))
        self.setMaximumWidth(220)

    @property
    def tab_id(self) -> str:
        return self._tab_id

    def set_active(self, active: bool) -> None:
        self.setProperty("active", bool(active))
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def set_state(self, tab: media_tabs.TabState, active: bool = False) -> None:
        """Refresh a tab already on the strip, without rebuilding its widgets."""
        self.title_label.setText(_elide(tab.title))
        self.meta_label.setText(tab.subtitle)
        self.setToolTip(f"{tab.title}\n{tab.subtitle}")
        self.close_button.setToolTip(f"Close {tab.title}")
        self.set_active(active)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if event.button() == Qt.LeftButton:
            self.activated.emit(self._tab_id)
        super().mouseReleaseEvent(event)


class MediaTabBar(QFrame):
    """The horizontal strip of open media tabs, scrolled like a browser's.

    The strip is chrome: a fullscreen player hides it with the rest of the
    window and the session puts it back when fullscreen ends.
    """

    tab_activated = Signal(str)
    tab_closed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mediaTabBar")
        self.setFixedHeight(40)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("mediaTabScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._host = QWidget()
        self._host.setObjectName("mediaTabHost")
        self._row = QHBoxLayout(self._host)
        self._row.setContentsMargins(0, 4, 0, 4)
        self._row.setSpacing(4)
        self._row.setAlignment(Qt.AlignLeft)
        self._scroll.setWidget(self._host)
        row.addWidget(self._scroll, 1)
        self._buttons: list[MediaTabButton] = []
        self._empty_note: QLabel | None = None

    # reading ---------------------------------------------------------------
    def buttons(self) -> list[MediaTabButton]:
        return list(self._buttons)

    def count(self) -> int:
        return len(self._buttons)

    def tab_ids(self) -> list[str]:
        return [button.tab_id for button in self._buttons]

    # rendering -------------------------------------------------------------
    def set_tabs(self, tabs: list[media_tabs.TabState], active_id: str | None) -> None:
        """Redraw the strip, or update it in place when the tabs are the same.

        Playback updates the active tab's subtitle, so the common case is the
        same tab ids in the same order: that only relabels the buttons.
        """
        if self._same_tabs(tabs):
            for button, tab in zip(self._buttons, tabs):
                button.set_state(tab, tab.tab_id == active_id)
            return
        for button in self._buttons:
            self._row.removeWidget(button)
            button.deleteLater()
        self._buttons = []
        if self._empty_note is not None:
            self._row.removeWidget(self._empty_note)
            self._empty_note.deleteLater()
            self._empty_note = None
        for tab in tabs:
            button = MediaTabButton(tab, tab.tab_id == active_id, self._host)
            button.activated.connect(self.tab_activated)
            button.closed.connect(self.tab_closed)
            self._row.addWidget(button, 0, Qt.AlignVCenter)
            self._buttons.append(button)
        self.setVisible(bool(self._buttons))
        self._scroll.setVisible(bool(self._buttons))

    def _same_tabs(self, tabs: list[media_tabs.TabState]) -> bool:
        return bool(tabs) and [tab.tab_id for tab in tabs] == self.tab_ids()


class MediaTabsController(QObject):
    """Keeps one :class:`MediaPage` per open tab and swaps them in a stack.

    The stack is the window's page stack, so a tab page looks exactly like any
    other application page; the tab bar above it says which media session is
    in front.
    """

    def __init__(self, stack: QStackedWidget, bar: MediaTabBar, *,
                 settings: object = None,
                 cache_root: Path | None = None,
                 player_factory: Callable[[QWidget], object] | None = None,
                 on_empty: Callable[[], None] | None = None,
                 on_show: Callable[[MediaPage, media_tabs.TabState], None] | None = None,
                 on_hub: Callable[[], None] | None = None) -> None:
        super().__init__(bar)
        self.stack = stack
        self.bar = bar
        self.settings = settings
        self.cache_root = cache_root
        self.player_factory = player_factory
        self.on_empty = on_empty
        self.on_show = on_show
        self.on_hub = on_hub
        self.store = media_tabs.TabStore()
        self._pages: dict[str, MediaPage] = {}
        #: Which tab this controller put in front last (the stack may be
        #: showing another application page in the meantime).
        self._shown: str | None = None
        #: True when the tab just closed was the page in front.
        self.closed_while_visible = False
        bar.tab_activated.connect(self.activate)
        bar.tab_closed.connect(self.close)

    # reading ---------------------------------------------------------------
    @property
    def tabs(self) -> list[media_tabs.TabState]:
        return self.store.tabs

    @property
    def active_id(self) -> str | None:
        return self.store.active_id

    def active_tab(self) -> media_tabs.TabState | None:
        return self.store.active

    def page_for(self, tab_id: str) -> MediaPage | None:
        return self._pages.get(tab_id)

    def active_page(self) -> MediaPage | None:
        tab = self.store.active
        return self._pages.get(tab.tab_id) if tab is not None else None

    # opening ---------------------------------------------------------------
    def open(self, card: object, section: str = "") -> MediaPage | None:
        """Open ``card`` in a new tab, or bring its open tab forward."""
        key = str(getattr(card, "key", "") or "")
        if not key:
            return None
        title = str(getattr(card, "title", "") or "")
        kind = str(getattr(card, "kind", "") or "")
        tab, created = self.store.open(key, title, kind)
        if section:
            self.store.update(tab.tab_id, section=section)
        page = self._pages.get(tab.tab_id)
        if page is None:
            page = self._new_page()
            self._pages[tab.tab_id] = page
            self.stack.addWidget(page)
        self.activate(tab.tab_id)
        current = page.detail_card()
        if created or current is None or current.key != key:
            page.open_card(card)
        return page

    def open_and_play(self, card: object, section: str = "") -> MediaPage | None:
        """Watch a title from the hub: its tab comes forward and plays there.

        The hub's own player is never used for this.  Opening or selecting the
        tab puts it in front first, which also stops anything the hub had
        playing, so two videos can never run at once.
        """
        page = self.open(card, section=section)
        if page is None:
            return None
        page.play_card(card)
        return page

    def _new_page(self) -> MediaPage:
        page = MediaPage(
            self.settings, self.cache_root,
            session_only=True, player_factory=self.player_factory,
        )
        page.tab_host = self
        return page

    # switching -------------------------------------------------------------
    @property
    def showing(self) -> bool:
        """True while the stack is showing one of this controller's pages."""
        return self._shown_id() is not None

    def show_active(self) -> bool:
        """Bring the active tab's page back, paused; False when there is none."""
        tab = self.store.active
        if tab is None or tab.tab_id not in self._pages:
            return False
        if self._shown_id() != tab.tab_id:
            self.activate(tab.tab_id)
        return self.stack.currentWidget() is self._pages.get(tab.tab_id)

    def suspend_shown(self) -> None:
        """Leaving the Media view: stop and free the tab that was in front.

        The tab keeps its page, its title selection and its saved position, so
        returning to it shows the paused panel instead of playing on.  Leaving
        the view also ends any fullscreen: the other pages are not the video.
        """
        self._suspend_current(keep_fullscreen=True)
        end_window_fullscreen(self.stack.window())
        self._shown = None
        self._sync_bar()

    def activate(self, tab_id: str) -> None:
        tab = self.store.activate(tab_id)
        if tab is None or tab_id == self._shown_id():
            return
        # Switching tabs keeps the window fullscreen only when the tab coming
        # in front still has a stream to watch: the strip stays usable and the
        # next tab keeps the same screen.  A details-only tab has no video, so
        # staying fullscreen there would hide the header and the rail with
        # nothing on screen and no player to catch Escape.  This mirrors GTK.
        keep_fullscreen = bool(getattr(tab, "url", "")) and in_window_fullscreen(
            self.stack.window()
        )
        self._suspend_current(tab_id, keep_fullscreen=keep_fullscreen)
        if not keep_fullscreen:
            # A paused tab left earlier has no player flag of its own, so the
            # window's session is ended here as well as by the player.
            end_window_fullscreen(self.stack.window())
        page = self._pages.get(tab_id)
        if page is None:
            return
        page.restore_tab(tab)
        self.stack.setCurrentWidget(page)
        self._shown = tab_id
        self._sync_bar()
        if self.on_show is not None:
            self.on_show(page, tab)

    def _shown_id(self) -> str | None:
        """The tab whose page the stack is actually showing, if any."""
        if self._shown is None:
            return None
        page = self._pages.get(self._shown)
        if page is None or self.stack.currentWidget() is not page:
            return None
        return self._shown

    def _suspend_current(self, keep_id: str | None = None,
                        keep_fullscreen: bool = False) -> None:
        shown = self._shown_id()
        if shown is None or shown == keep_id:
            return
        page = self._pages.get(shown)
        self._capture(shown)
        if page is not None:
            page.suspend_tab(keep_fullscreen=keep_fullscreen)

    def _capture(self, tab_id: str) -> None:
        page = self._pages.get(tab_id)
        if page is None:
            return
        self.store.update(tab_id, **page.tab_snapshot())

    def back(self, page: MediaPage) -> None:
        """A tab's Back: save it, then show the Movies & TV hub, tab kept."""
        tab_id = self._shown_id()
        if tab_id is not None:
            self._capture(tab_id)
            page.suspend_tab()
            self._shown = None
        self._sync_bar()
        if self.on_hub is not None:
            self.on_hub()

    def playback_changed(self, page: MediaPage) -> None:
        """A tab started playing an episode: keep its subtitle in step."""
        for tab_id, known in self._pages.items():
            if known is page:
                self._capture(tab_id)
                self._sync_bar()
                return

    # closing ---------------------------------------------------------------
    def close(self, tab_id: str) -> None:
        was_active = self.store.active_id == tab_id
        page = self._pages.get(tab_id)
        # Closing a tab whose page is not in front must not navigate.
        was_visible = page is not None and self.stack.currentWidget() is page
        self._pages.pop(tab_id, None)
        if self._shown == tab_id:
            self._shown = None
        new_active = self.store.close(tab_id)
        self.closed_while_visible = was_visible
        if page is not None:
            page.close_tab()
            self.stack.removeWidget(page)
            page.deleteLater()
        if was_active and new_active is not None and was_visible:
            # Closing the tab in front ends the fullscreen it was showing, even
            # when the tab that follows is only paused: a paused panel is not
            # the video the whole screen was given to.  GTK does the same.
            end_window_fullscreen(self.stack.window())
            self.activate(new_active.tab_id)
            return
        self._sync_bar()
        if was_active and self.store.empty:
            # Closing the last tab ends the session the tab strip belonged to.
            end_window_fullscreen(self.stack.window())
            if self.on_empty is not None:
                self.on_empty()

    def close_active(self) -> None:
        tab = self.store.active
        if tab is not None:
            self.close(tab.tab_id)

    def close_all(self) -> None:
        end_window_fullscreen(self.stack.window())
        for tab in self.store.tabs:
            page = self._pages.pop(tab.tab_id, None)
            if page is not None:
                page.close_tab()
                self.stack.removeWidget(page)
                page.deleteLater()
        self.store.clear()
        self._shown = None
        self.bar.set_tabs([], None)
        self.bar.setVisible(False)

    # strip -----------------------------------------------------------------
    def _sync_bar(self) -> None:
        tabs = self.store.tabs
        self.bar.set_tabs(tabs, self.store.active_id)
        # A fullscreen player owns the whole screen: a tab switch or a resume
        # must not bring the strip back before the session ends.
        self.bar.setVisible(
            bool(tabs) and not in_window_fullscreen(self.stack.window())
        )


__all__ = ["MediaTabBar", "MediaTabButton", "MediaTabsController"]
