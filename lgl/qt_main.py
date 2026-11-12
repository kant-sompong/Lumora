"""Qt 6 (PySide6) frontend for Lumora — the Windows UI.

This mirrors the GTK frontend (``ui.py``): the same icon rail, the same header
with back/forward, a centred search field and a menu, and the same four
destinations (Browse, Library, Downloads, Media) plus Settings and About.
Colours, radii and type sizes come from ``theme.py`` through ``qt_theme``, and
the section layouts mirror the GTK pages so the two builds feel identical.

Only the platform-neutral core is used for data (``ArchiveClient``,
``LibraryStore``, ``DownloadManager``, ``settings``); nothing here imports GTK.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import discovery
from . import nav_layout
from . import surprise_pool
from . import profiles
from . import recently_viewed
from . import suggestions
from . import view_prefs
from . import __version__, magnet as magnetlib, platform_support, theme
from .archive import ArchiveClient, build_download_url
from .catalog_store import CatalogStore
from .downloader import DownloadManager, TaskSnapshot
from .library_store import LibraryStore
from .qt_discover import (
    ActivityTimelineDialog,
    DiscoverDialog,
    FavouritesDialog,
)
from .qt_library_tools import (
    DuplicatesDialog,
    TagsDialog,
    as_entry,
)
from .qt_recent import RecentlyViewedDialog
from .qt_stats import StatisticsDialog
from .qt_browse import BrowsePage
from .qt_downloads import (
    DownloadBridge,
    DownloadsPage,
    FilePickDialog,
    downloadable_files,
    enqueue_files,
)
from .qt_icons import icon
from .qt_library import LibraryPage
from .qt_media import MediaPage
from .qt_settings import AboutDialog, SettingsDialog
from .qt_theme import build_stylesheet
from .qt_widgets import (
    IconButton,
    RailButton,
    Toast,
    color,
    run_async,
    set_theme,
)
from .settings import Settings

APP_TITLE = "Lumora"
RAIL_WIDTH = 72
COMPACT_RAIL_WIDTH = 64
HEADER_HEIGHT = 64
COMPACT_HEADER_HEIGHT = 56
SEARCH_HEIGHT = 40
COMPACT_SEARCH_HEIGHT = 34
#: Length of the page fade, in milliseconds, and of the longer "full" amount.
TRANSITION_MS = 150
FULL_TRANSITION_MS = 240


class Window(QMainWindow):
    """The main window: rail + header + one page per destination."""

    def __init__(self, store: LibraryStore | None = None,
                 client: ArchiveClient | None = None,
                 manager: DownloadManager | None = None,
                 download_dir: Path | None = None,
                 settings: Settings | None = None) -> None:
        super().__init__()
        self._settings = settings or Settings()
        # The profile whose files this window was built from, so the header can
        # say what a switch will change only after a restart.
        try:
            self._session_profile = profiles.active_profile().name
        except Exception:  # noqa: BLE001 - a header label must never break
            self._session_profile = ""
        self.client = client
        self.manager = manager
        self.download_dir = Path(download_dir) if download_dir else platform_support.default_download_dir()
        self.bridge = DownloadBridge()
        self._history: list[str] = []
        self._history_index = -1
        self._titles: dict[str, str] = {}
        self.catalog = CatalogStore(platform_support.data_dir() / "catalog.json")
        self.setWindowTitle(APP_TITLE)
        width, height = self._settings.window_size
        self.resize(width, height)
        self.setMinimumSize(980, 640)
        if self._settings.maximized:
            self.showMaximized()

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_rail())

        body = QWidget()
        body.setObjectName("pageBody")
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_header())

        self.stack = QStackedWidget()
        column.addWidget(self.stack, 1)
        shell.addWidget(body, 1)

        self.browse = BrowsePage(
            client, cache_root=platform_support.cache_dir(), catalog=self.catalog,
            settings=self._settings,
            rows_provider=self._discover_rows,
            liked_provider=self._liked_keys,
        )
        self.browse.item_requested.connect(self._open_browse_row)
        self.browse.download_requested.connect(self._download_requested)
        self.browse.files_download_requested.connect(self._files_download_requested)
        self.browse.catalog_download_requested.connect(self._catalog_download_requested)
        self.browse.count_changed.connect(self._browse_count_changed)
        self.browse.reload_catalog()
        self.stack.addWidget(self.browse)

        self.library = LibraryPage(
            store, self._settings, client, self.download_dir,
            candidates=self._catalogue_rows,
        )
        self.library.magnet_requested.connect(self.add_magnet)
        self.stack.addWidget(self.library)

        self.downloads: DownloadsPage | None = None
        if manager is not None:
            self.torrents = self._torrent_manager()
            self.downloads = DownloadsPage(
                manager, lambda: self.download_dir, self._title_for,
                torrents=self.torrents, settings=self._settings,
            )
            self.downloads.count_changed.connect(self._downloads_count)
            self.bridge.changed.connect(self.downloads.schedule_refresh)
            self.stack.addWidget(self.downloads)

        self.media = MediaPage(self._settings, platform_support.cache_dir())
        self.media.configure_requested.connect(lambda: self.show_settings("providers"))
        self.media.surprise_requested.connect(self.pick_random)
        self.stack.addWidget(self.media)

        self.toast = Toast(root)
        # The interface preferences are applied before the first page is shown,
        # so a restart opens with the compact, accent and motion the user chose.
        self._compact = False
        self._accent = view_prefs.DEFAULT_ACCENT
        self._transition_mode = "subtle"
        self._transition_anim: QPropertyAnimation | None = None
        self._palette: Any = None
        self.apply_interface_prefs()
        self._install_shortcuts()
        self.show_view("Library")
        self._sync_rail()
        if store is not None:
            self.library.set_entries(store.entries())
        if self._settings.get("rescan_on_startup", True) and store is not None:
            QTimer.singleShot(400, self.library.rescan)
        QTimer.singleShot(250, lambda: self.browse.load())

    # construction ---------------------------------------------------------
    def _torrent_manager(self):
        """The magnet queue, owned by the window like the download queue."""
        try:
            from .torrent import TorrentManager

            return TorrentManager(
                platform_support.data_dir() / "torrents.json",
                self.download_dir,
                engine_path=self._settings.torrent_client,
            )
        except Exception:  # noqa: BLE001 - the strip simply stays hidden
            return None

    def _build_rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(RAIL_WIDTH)
        self._rail = rail
        column = QVBoxLayout(rail)
        column.setContentsMargins(4, 10, 4, 10)
        column.setSpacing(2)
        self.rail_buttons: dict[str, RailButton] = {}
        for key, icon_name, text, tip in (
            ("browse", "grid", "Browse", "Browse the collection (Ctrl+1)"),
            ("library", "library", "Library", "Your downloaded games (Ctrl+2)"),
            ("media", "media", "Media", "Movies & TV (Ctrl+4)"),
            ("downloads", "downloads", "Downloads", "The download queue (Ctrl+3)"),
        ):
            button = RailButton(key, icon_name, text, tip)
            button.clicked.connect(lambda k=key: self.show_view(k))
            column.addWidget(button, 0, Qt.AlignHCenter)
            self.rail_buttons[key] = button
        column.addStretch(1)
        for key, icon_name, text, tip, callback in (
            ("settings", "settings", "Settings", "Settings (Ctrl+,)", self.show_settings),
            ("about", "about", "About", "About this application", self.show_about),
        ):
            button = RailButton(key, icon_name, text, tip)
            button.clicked.connect(callback)
            column.addWidget(button, 0, Qt.AlignHCenter)
            self.rail_buttons[key] = button
        return rail

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("headerBar")
        header.setFixedHeight(HEADER_HEIGHT)
        self._header = header
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 0, 14, 0)
        row.setSpacing(6)

        self.back_btn = IconButton("back", "Back", 17, 34)
        self.back_btn.clicked.connect(self.go_back)
        self.back_btn.setEnabled(False)
        row.addWidget(self.back_btn)
        self.forward_btn = IconButton("forward", "Forward", 17, 34)
        self.forward_btn.clicked.connect(self.go_forward)
        self.forward_btn.setEnabled(False)
        row.addWidget(self.forward_btn)
        row.addStretch(1)

        pill = QFrame()
        pill.setObjectName("searchPill")
        pill.setFixedHeight(SEARCH_HEIGHT)
        self._search_pill = pill
        pill.setMinimumWidth(360)
        pill.setMaximumWidth(520)
        pill_row = QHBoxLayout(pill)
        pill_row.setContentsMargins(14, 0, 12, 0)
        pill_row.setSpacing(8)
        magnifier = QLabel()
        magnifier.setPixmap(
            icon("search", 15, color("faint"), 1.7).pixmap(15, 15)
        )
        pill_row.addWidget(magnifier)
        self.search_field = QLineEdit()
        self.search_field.setObjectName("searchField")
        self.search_field.setPlaceholderText("Search games")
        self.search_field.textChanged.connect(self._on_search_changed)
        pill_row.addWidget(self.search_field, 1)
        hint = QLabel("Ctrl K")
        hint.setObjectName("keyHint")
        pill_row.addWidget(hint)
        row.addWidget(pill, 0)
        row.addStretch(1)

        # Right-hand cluster: the die sits with the other page actions.
        self.menu_btn = IconButton("menu", "Menu", 18, 34)
        self.menu_btn.clicked.connect(self._show_menu)
        row.addWidget(self.menu_btn)
        self.refresh_btn = IconButton("refresh", "Refresh (reload collection, rescan library)", 17, 34)
        self.refresh_btn.clicked.connect(self.refresh_all)
        row.addWidget(self.refresh_btn)

        # The profile in use, so it is obvious which library and settings are open.
        self.profile_label = QLabel("", header)
        self.profile_label.setObjectName("keyHint")
        self.profile_label.setToolTip("Active profile · Library tools → Profiles… to switch")
        row.addWidget(self.profile_label)
        self.refresh_profile_label()
        return header

    def refresh_profile_label(self, was: str | None = None) -> None:
        """Show the active profile; after a switch, show where it came from."""
        try:
            name = profiles.load().active().name
        except Exception:  # noqa: BLE001 - a header label must never break the window
            return
        if was and was != name:
            self.profile_label.setText(f"Profile · {was} → {name}")
        else:
            self.profile_label.setText(f"Profile · {name}")

    def _install_shortcuts(self) -> None:
        from . import shortcut_prefs
        from .qt_shortcuts import DEFAULTS

        keys = shortcut_prefs.effective(self._settings, DEFAULTS)
        for shortcut_id, callback in (
            ("nav.browse", lambda: self.show_view("browse")),
            ("nav.library", lambda: self.show_view("library")),
            ("nav.downloads", lambda: self.show_view("downloads")),
            ("nav.media", lambda: self.show_view("media")),
            ("search", self.focus_search),
            ("refresh", self.refresh_all),
            ("settings", lambda: self.show_settings()),
        ):
            sequence = keys.get(shortcut_id) or DEFAULTS[shortcut_id]
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
        for extra, callback in (("Ctrl+K", self.show_palette), ("F5", self.refresh_all)):
            QShortcut(QKeySequence(extra), self).activated.connect(callback)

    def show_palette(self) -> Any:
        """Open the command palette; Ctrl+K and the menu both land here."""
        from .qt_palette import show_palette

        self._palette = show_palette(self)
        return self._palette

    # navigation -----------------------------------------------------------
    VIEW_TITLES = {
        "browse": "Browse", "library": "Library",
        "downloads": "Downloads", "media": "Movies & TV",
    }

    def show_view(self, name: str) -> None:
        aliases = {
            "Archive": "browse", "Browse": "browse", "browse": "browse",
            "Library": "library", "library": "library",
            "Downloads": "downloads", "downloads": "downloads",
            "Media": "media", "media": "media",
        }
        key = aliases.get(str(name), "browse")
        if key == "downloads" and self.downloads is None:
            self.toast.show_message("Downloads are not available.", error=True)
            return
        page = {
            "browse": self.browse,
            "library": self.library,
            "downloads": self.downloads,
            "media": self.media,
        }[key]
        self._transition_to(page)  # type: ignore[arg-type]
        self.browse.detail.hide() if key != "browse" else None
        self._remember(key)
        self._current = key
        self._sync_rail()
        self._sync_search_field(key)
        if key == "browse":
            self.browse.load()
        elif key == "media":
            self.media.show_section(self.media._section)
            QTimer.singleShot(0, self.media.reload) if not self.media._loaded else None

    def _remember(self, key: str) -> None:
        if self._history_index >= 0 and self._history[self._history_index] == key:
            return
        self._history = self._history[: self._history_index + 1] + [key]
        self._history_index = len(self._history) - 1
        self._update_history_buttons()

    def go_back(self) -> None:
        if self._current == "browse" and self.browse.detail.isVisible():
            self.browse.clear_selection()
            return
        if self._history_index <= 0:
            return
        self._history_index -= 1
        self._apply_history(self._history[self._history_index])

    def go_forward(self) -> None:
        if self._history_index >= len(self._history) - 1:
            return
        self._history_index += 1
        self._apply_history(self._history[self._history_index])

    def _apply_history(self, key: str) -> None:
        page = {
            "browse": self.browse, "library": self.library,
            "downloads": self.downloads, "media": self.media,
        }.get(key)
        if page is None:
            return
        self._transition_to(page)
        self._current = key
        self._sync_rail()
        self._sync_search_field(key)
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.back_btn.setEnabled(self._history_index > 0)
        self.forward_btn.setEnabled(self._history_index < len(self._history) - 1)

    def _sync_rail(self) -> None:
        current = getattr(self, "_current", "library")
        for key, button in self.rail_buttons.items():
            button.set_active(key == current)

    def _browse_count_changed(self, total: int) -> None:
        """Keep the header placeholder in step with the collection size."""
        self._browse_total = total
        if getattr(self, "_current", "library") == "browse":
            self.search_field.setPlaceholderText(self._browse_placeholder())

    def _browse_placeholder(self) -> str:
        return "Search games"

    def _sync_search_field(self, key: str) -> None:
        if key == "browse":
            self.search_field.setPlaceholderText(self._browse_placeholder())
            self.search_field.setEnabled(True)
        elif key == "library":
            self.search_field.setPlaceholderText("Search library")
            self.search_field.setEnabled(True)
            library = getattr(self, "library", None)
            if library is not None:
                # The page has no box of its own, so whatever the header holds
                # is the query the Games tab filters by.
                library.set_query(self.search_field.text())
        elif key == "media":
            self.search_field.setPlaceholderText("Search movies, TV, anime")
            self.search_field.setEnabled(True)
        else:
            self.search_field.setPlaceholderText("Search")
            self.search_field.setEnabled(False)

    def focus_search(self) -> None:
        self._prepare_suggestions()
        self.search_field.setFocus()
        self.search_field.selectAll()

    def _on_search_changed(self, text: str) -> None:
        current = getattr(self, "_current", "library")
        if current == "browse":
            self.browse.refresh()
        elif current == "library":
            self.library.set_query(text)
        elif current == "media":
            self.media.search.setText(text)

    def refresh_all(self) -> None:
        try:
            self.library.rescan()
        except Exception:  # noqa: BLE001 - a failed rescan must not stop a reload
            pass
        try:
            self.browse.load(force=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.media.reload()
        except Exception:  # noqa: BLE001
            pass

    # interface preferences -------------------------------------------------
    def apply_interface_prefs(self) -> None:
        """Apply compact mode, the accent colour and the transition amount.

        Called on start-up and again from the Settings sheet, so every switch
        there has an effect without a restart.  A preference that cannot be read
        falls back to the shipped look rather than failing.
        """
        try:
            prefs = view_prefs.effective(self._settings)
        except (AttributeError, TypeError, ValueError):
            return
        self._compact = bool(prefs[view_prefs.COMPACT])
        self._accent = str(prefs[view_prefs.ACCENT])
        self._transition_mode = str(prefs[view_prefs.TRANSITIONS])
        self.apply_theme(self._settings.theme)
        header = getattr(self, "_header", None)
        if header is not None:
            header.setFixedHeight(
                COMPACT_HEADER_HEIGHT if self._compact else HEADER_HEIGHT
            )
        pill = getattr(self, "_search_pill", None)
        if pill is not None:
            pill.setFixedHeight(
                COMPACT_SEARCH_HEIGHT if self._compact else SEARCH_HEIGHT
            )
        rail = getattr(self, "_rail", None)
        if rail is not None:
            rail.setFixedWidth(COMPACT_RAIL_WIDTH if self._compact else RAIL_WIDTH)
        for page in (
            getattr(self, "browse", None),
            getattr(self, "library", None),
            getattr(self, "media", None),
        ):
            setter = getattr(page, "set_compact", None)
            if callable(setter):
                setter(self._compact)
        self.update()

    def transition_duration(self) -> int:
        """Milliseconds a page fade takes; ``0`` means switch instantly."""
        if self._transition_mode == "off":
            return 0
        return FULL_TRANSITION_MS if self._transition_mode == "full" else TRANSITION_MS

    def _transition_to(self, page: QWidget | None) -> None:
        """Show ``page``, fading it in unless transitions are off."""
        if page is None or page is self.stack.currentWidget():
            return
        duration = self.transition_duration()
        if duration <= 0:
            self.stack.setCurrentWidget(page)
            return
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        self.stack.setCurrentWidget(page)
        self._transition_anim = animation
        animation.start()

    # menu / dialogs -------------------------------------------------------
    def _build_menu(self) -> QMenu:
        """The header menu, built but not shown, so tests can inspect it."""
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        maintenance_menu: QMenu | None = None
        maintenance = (
            ("Rescan library", self.library.rescan),
            ("Reload collection", lambda: self.browse.load(force=True)),
            ("Download from catalogue…", self.show_catalog_downloads),
            ("Sync catalogue artwork", lambda: self.sync_artwork(False)),
            ("Re-check games with no artwork match", lambda: self.sync_artwork(True)),
            ("Clear image cache", self.clear_image_cache),
            ("Open data folder",
             lambda: platform_support.open_folder(platform_support.data_dir())),
            ("Open download folder",
             lambda: platform_support.open_folder(self.download_dir)),
        )
        # The menu holds exactly what nav_layout lists: everything else moved
        # into the pages it belongs to, where people look for it.
        callbacks = {
            "palette": self.show_palette,
            "profiles": self.show_profiles,
            "shortcuts": self.show_shortcuts,
            "tools": self.show_tools,
            "sources": self.show_sources,
            "kryoto": self.show_kryoto_catalog,
            "queue": lambda: self.show_view("downloads"),
            "settings": self.show_settings,
            "about": self.show_about,
        }
        for key, text in nav_layout.MENU:
            if key == "maintenance":
                maintenance_menu = menu.addMenu(text)
                for label, slot in maintenance:
                    action = maintenance_menu.addAction(label)
                    action.triggered.connect(slot)
                continue
            action = menu.addAction(text)
            action.triggered.connect(callbacks[key])
            if key == "palette":
                action.setShortcut(QKeySequence("Ctrl+K"))
                menu.addSeparator()
            elif key == "tools":
                menu.addSeparator()
            elif key == "queue":
                menu.addSeparator()
        return menu

    def _show_menu(self) -> None:
        menu = self._build_menu()
        menu.exec(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft()))

    def show_sources(self) -> None:
        from .qt_catalog import CatalogDialog

        CatalogDialog(
            self, catalog=self.catalog, on_imported=self._catalog_imported
        ).exec()

    def show_kryoto_catalog(self) -> None:
        from .qt_catalog import KRYOTO_NOTE, KRYOTO_URL, CatalogDialog

        CatalogDialog(
            self, title="Kryoto Catalog", url=KRYOTO_URL, note=KRYOTO_NOTE,
            action="Import all", catalog=self.catalog,
            on_imported=self._catalog_imported,
        ).exec()

    def _catalog_imported(self, count: int) -> None:
        """Show what an import added, the way the GTK build does after Kryoto."""
        listed = self.browse.reload_catalog()
        if listed:
            self.toast.show_message(
                f"Browse now lists {listed:,} imported catalogue game(s).", ms=4000
            )
        if self._current != "browse":
            self.show_view("browse")
        else:
            self.browse.refresh()

    def show_calendar(self) -> None:
        """Open the release calendar: what airs or releases on which day."""
        from .qt_calendar import show_calendar

        show_calendar(self.settings, self)

    # places -----------------------------------------------------------------
    def show_place(self, page: str, tab: str = "") -> None:
        """Go to a page and, when given, bring one of its tabs to the front."""
        key = "browse" if page == "archive" else str(page or "browse")
        if key == "library":
            self.show_view("library")
            if tab:
                self.library.show_tab(tab)
            return
        if key == "media":
            self.show_view("media")
            if tab:
                self.media.show_section(tab)
            return
        self.show_view(key)

    def _discover_rows(self) -> list[dict]:
        """Local rows the discovery ranks use: the library and the media cards."""
        return self._library_rows() + self._media_rows()

    def _liked_keys(self) -> list[str]:
        return [row["key"] for row in self._library_rows() if row.get("favorite")]

    def _catalogue_rows(self) -> list[dict]:
        """The loaded catalogue as rows, so the library can fill its own gaps."""
        browse = getattr(self, "browse", None)
        games = getattr(browse, "_games", None) or []  # noqa: SLF001
        return [
            {
                "key": game.identifier,
                "title": game.title,
                "year": game.year,
                "tags": list(game.subjects or ()),
            }
            for game in games
        ]

    def _open_browse_row(self, item: object) -> None:
        """Open one poster from Browse's discovery rows."""
        key = str(item.get("key") or "") if isinstance(item, dict) else ""
        if not key:
            return
        if self.browse.game(key) is not None:
            self.show_view("browse")
            self.browse.select(key)
            return
        card = self.media.card(key)
        if card is not None:
            self.show_view("media")
            self.media.open_card(card)

    def show_tools(self) -> None:
        """Open the tools window: providers, downloads, backup, profiles and more."""
        from .qt_tools import show_tools

        show_tools(self.settings, self)

    def show_recent(self) -> None:
        dialog = RecentlyViewedDialog(recently_viewed.default_path(), self)
        dialog.open_requested.connect(self.open_recent)
        dialog.exec()

    def open_recent(self, kind: str, key: str) -> None:
        """Go to a title picked in the Recently viewed window."""
        if kind == "game":
            self.show_view("library")
            self.library.select(key)
            return
        card = self.media.card(key)
        self.show_view("media")
        if card is not None:
            self.media.open_card(card)

    def pick_random(self) -> None:
        """Open one random title from the page the user is looking at."""
        page = getattr(self, "_current", "") or "library"
        games: list = []
        media: list = []
        section = ""
        if page == "library":
            games = list(self.library._entries)  # noqa: SLF001
        elif page == "browse":
            browse = getattr(self, "browse", None)
            games = list(
                getattr(browse, "_shown", None)
                or getattr(browse, "_games", None)
                or ()
            )
        elif page == "media":
            media = self.media._all_cards()  # noqa: SLF001
            section = str(getattr(self.media, "_section", "") or "")
        else:
            games = list(self.library._entries)  # noqa: SLF001
            media = self.media._all_cards()  # noqa: SLF001
        pool = surprise_pool.pool_for(
            page, games=games, media=media, section=section
        )
        choice = discovery.random_pick(pool)
        if choice is None:
            self.toast.show_message(
                "Nothing to pick yet — add a game or open Movies & TV.", error=True
            )
            return
        self.toast.show_message(f"Random pick: {choice['title']}")
        self.open_recent(choice["kind"], choice["key"])

    def show_statistics(self) -> None:
        dialog = StatisticsDialog(
            list(self.library._entries),  # noqa: SLF001
            recently_viewed.recent(recently_viewed.default_path(), 100),
            self,
        )
        dialog.exec()

    # library tools --------------------------------------------------------
    def _library_rows(self) -> list[dict]:
        # Browse asks for these while the window is still being built, so a page
        # that does not exist yet simply offers no rows.
        library = getattr(self, "library", None)
        entries = getattr(library, "_entries", None) or []
        return [as_entry(entry) for entry in entries]

    def _media_rows(self) -> list[dict]:
        media = getattr(self, "media", None)
        cards = media._all_cards() if media is not None else []  # noqa: SLF001
        return [
            {
                "key": card.key,
                "title": card.title,
                "year": card.year,
                "rating": card.rating,
                "kind": card.kind,
            }
            for card in cards
        ]

    def show_tags(self) -> None:
        TagsDialog(list(self.library._entries), self._settings, self).exec()  # noqa: SLF001

    def show_duplicates(self) -> None:
        DuplicatesDialog(self._library_rows(), self).exec()

    def show_discover(self) -> None:
        """Recommendations, hidden gems, similarity and collection gaps."""
        rows = self._library_rows() + self._media_rows()
        liked = [row["key"] for row in self._library_rows() if row.get("favorite")]
        owned = [row["key"] for row in rows]
        DiscoverDialog(rows, liked, owned, self, settings=self._settings).exec()

    def show_favourites(self) -> None:
        rows = [row | {"kind": "game"} for row in self._library_rows() if row.get("favorite")]
        FavouritesDialog(rows, self).exec()

    def show_profiles(self) -> None:
        """Choose which profile's library and settings the next start uses."""
        from .qt_profiles import ProfilesDialog

        dialog = ProfilesDialog(profiles.load(), self, on_switch=self._profile_switched)
        dialog.exec()
        self.refresh_profile_label()

    def _profile_switched(self, profile) -> None:
        """The active profile changed; it is read at the next start."""
        self.refresh_profile_label(was=self._session_profile)
        try:
            self.toast.show_message(f"Restart Lumora to switch to {profile.name}.")
        except Exception:  # noqa: BLE001 - the toast is decoration only
            pass

    def show_shortcuts(self) -> None:
        """Rebind the keys; the window picks them up the next time it opens."""
        from .qt_shortcuts import ShortcutsDialog

        ShortcutsDialog(self._settings, self).exec()

    def show_collections(self) -> None:
        """Create collections and put library titles in them."""
        from .collections import COLLECTIONS_FILENAME, CollectionStore
        from .qt_collections import CollectionsDialog

        store = CollectionStore(profiles.active_file(COLLECTIONS_FILENAME))
        CollectionsDialog(store, list(self.library._entries), self).exec()  # noqa: SLF001

    def show_activity(self) -> None:
        from .qt_stats import build_events

        events = build_events(
            list(self.library._entries),  # noqa: SLF001
            recently_viewed.recent(recently_viewed.default_path(), 200),
        )
        ActivityTimelineDialog(events, self).exec()

    def _search_titles(self) -> list[str]:
        titles = [entry.title for entry in self.library._entries]  # noqa: SLF001
        titles += [card.title for card in self.media._all_cards()]  # noqa: SLF001
        return [title for title in titles if title]

    def _update_suggestions(self, text: str) -> None:
        if getattr(self, "_search_model", None) is None:
            return
        matches = suggestions.suggest(text, self._search_titles(), limit=8)
        self._search_model.setStringList([match.text for match in matches])

    def _prepare_suggestions(self) -> None:
        if getattr(self, "_search_completer", None) is not None:
            self._update_suggestions(self.search_field.text())
            return
        from PySide6.QtCore import QStringListModel
        from PySide6.QtWidgets import QCompleter

        self._search_model = QStringListModel([], self)
        self._search_completer = QCompleter(self._search_model, self)
        self._search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._search_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.search_field.setCompleter(self._search_completer)
        self.search_field.textChanged.connect(self._update_suggestions)
        self._update_suggestions(self.search_field.text())

    def show_settings(self, tab: str = "general") -> None:
        dialog = SettingsDialog(self._settings, self)
        dialog.show_tab(tab)
        dialog.exec()
        self.apply_interface_prefs()

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def download_dir_changed(self, path: Path) -> None:
        self.download_dir = Path(path)
        self.library.download_dir = Path(path)
        self.library.rescan()

    # downloads ------------------------------------------------------------
    def toggle_downloads(self) -> None:
        """Ctrl+D: open the download queue, or go back where you were."""
        if self.downloads is None:
            return
        if getattr(self, "_current", "library") == "downloads":
            self.show_view(getattr(self, "_previous_view", "library"))
        else:
            self._previous_view = getattr(self, "_current", "library")
            self.show_view("downloads")

    def _downloads_count(self, active: int) -> None:
        button = self.rail_buttons.get("downloads")
        if button is not None:
            button.set_badge(active)

    def _game(self, identifier: str):
        return self.browse.game(identifier)

    def _title_for(self, identifier: str) -> str:
        if identifier in self._titles:
            return self._titles[identifier]
        game = self._game(identifier)
        return (game.title if game else "") or identifier

    def _download_requested(self, identifier: str) -> None:
        game = self._game(identifier)
        if self.manager is None or self.client is None or game is None:
            self.toast.show_message("Downloads are not available.", error=True)
            return
        if game.is_catalog:
            self.toast.show_message(
                "Downloads from imported catalogs are not available in this build yet.",
                error=True,
            )
            return
        client = self.client
        self.toast.show_message("Looking up files…", ms=2500)

        def failed(exc: object) -> None:
            self.toast.show_message(f"Could not load this game: {exc}", error=True)

        def got_item(item: object) -> None:
            if getattr(item, "restricted", False):
                self.toast.show_message(
                    "This archive item is restricted; downloads are unavailable.", error=True
                )
                return
            files = downloadable_files(item)  # type: ignore[arg-type]
            if not files:
                self.toast.show_message("No downloadable files were found.", error=True)
                return
            if len(files) > 1:
                dialog = FilePickDialog(game.title or identifier, files, self)
                if dialog.exec() != FilePickDialog.Accepted:
                    return
                files = dialog.chosen()
                if not files:
                    self.toast.show_message("No files were selected.", error=True)
                    return
            enqueue_files(
                self.manager, item, files, self.download_dir,  # type: ignore[arg-type]
                on_done=self._queued,
                on_error=lambda exc: self.toast.show_message(
                    f"Could not queue: {exc}", error=True
                ),
            )

        run_async(lambda: client.fetch_item(identifier), got_item, failed)

    def _queued(self, added: int, errors: list[str], note: str | None) -> None:
        plural = "file" if added == 1 else "files"
        if added and not errors and not note:
            self.toast.show_message(f"Added {added} {plural} to Downloads.")
        elif added:
            detail = errors[0] if errors else (note or "")
            self.toast.show_message(
                f"Added {added} {plural}. {len(errors)} could not be added: {detail}"
                if errors else f"Added {added} {plural}. {detail}",
                error=True,
            )
        else:
            self.toast.show_message(errors[0] if errors else "Nothing was added.", error=True)
        if self.downloads is not None:
            self.downloads.refresh()

    # catalogue and torrents -----------------------------------------------
    def _files_download_requested(self, payload: object) -> None:
        """Queue an explicit file selection chosen in the Browse detail panel."""
        if self.manager is None:
            self.toast.show_message("Downloads are not available.", error=True)
            return
        item, files = payload  # type: ignore[misc]
        enqueue_files(
            self.manager, item, files, self.download_dir,
            on_done=self._queued,
            on_error=lambda exc: self.toast.show_message(
                f"Could not queue: {exc}", error=True
            ),
        )

    def show_catalog_downloads(self) -> None:
        from .qt_catalog import CatalogDownloadDialog

        CatalogDownloadDialog(
            self, self.catalog, self.manager, getattr(self, "torrents", None),
            self.download_dir, on_queued=self._catalog_queued,
        ).exec()

    def _catalog_download_requested(self, identifier: str) -> None:
        self.show_catalog_downloads()

    def _catalog_queued(self, identifier: str, title: str) -> None:
        self._titles[identifier] = title

    def add_magnet(self, identifier: str) -> None:
        """Attach a magnet link to a library game with the torrent queue."""
        entry = next((e for e in self.library._entries if e.identifier == identifier), None)
        title = entry.title if entry is not None else identifier
        text, ok = QInputDialog.getText(
            self, "Add magnet link",
            f"Magnet URI for {title or identifier}:",
        )
        if not ok or not text.strip():
            return
        try:
            link = magnetlib.parse_magnet(text.strip())
        except Exception as exc:  # noqa: BLE001 - reported in the toast
            self.toast.show_message(f"That is not a usable magnet link: {exc}", error=True)
            return
        torrents = getattr(self, "torrents", None)
        if torrents is None:
            self.toast.show_message("The torrent queue is unavailable.", error=True)
            return
        try:
            torrents.add(link.uri, title=title or link.display_name)
        except Exception as exc:  # noqa: BLE001
            self.toast.show_message(f"Could not add the magnet: {exc}", error=True)
            return
        self.toast.show_message(f"Added {title or link.display_name or 'the magnet'} to Torrents.")
        if self.downloads is not None:
            self.show_view("downloads")

    def sync_artwork(self, force: bool = False) -> None:
        """Match posters and details for imported catalogue records."""
        from .qt_artwork import sync_catalog_artwork

        catalog = self.catalog
        if catalog.count_games() == 0:
            self.toast.show_message("No catalogue has been imported yet.")
            return
        self.toast.show_message("Matching artwork for imported games…", ms=3000)

        def done(summary: object) -> None:
            data = summary if isinstance(summary, dict) else {}
            self.toast.show_message(
                f"Artwork: {data.get('added', 0)} filled, "
                f"{data.get('unchanged', 0)} unchanged, "
                f"{data.get('missing', 0)} with no match, "
                f"{data.get('failed', 0)} failed.",
                error=bool(data.get('failed')) and not data.get('added'),
            )

        run_async(
            lambda: sync_catalog_artwork(catalog, self._settings, force=force),
            done,
            lambda exc: self.toast.show_message(
                f"Artwork sync failed: {exc}", error=True
            ),
        )

    def check_updates(self) -> None:
        """Ask GitHub for the latest release; never install anything."""
        from .updates import check_for_update

        def work() -> object:
            return check_for_update(__version__)

        def done(status: object) -> None:
            newer = bool(getattr(status, "newer", False))
            message = str(getattr(status, "message", ""))
            url = str(getattr(status, "url", ""))
            if not newer:
                self.toast.show_message(
                    message or "You are running the latest release."
                )
                return
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setWindowTitle("Update available")
            box.setText(message)
            box.setInformativeText(
                "Lumora never installs updates by itself. Open the release page to "
                "download the new build."
            )
            open_btn = box.addButton("Open release page", QMessageBox.AcceptRole)
            box.addButton("Later", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn and url:
                platform_support.open_url(url)

        def failed(exc: object) -> None:
            self.toast.show_message(f"Update check failed: {exc}", error=True)

        run_async(work, done, failed)

    # lifecycle ------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # The window is resized inside __init__, before the toast exists, and
        # some platforms (Windows) deliver that event immediately.
        toast = getattr(self, "toast", None)
        if toast is not None:
            toast.reposition()

    def apply_theme(self, mode: str) -> None:
        """Re-style the running window when Settings changes the theme."""
        app = QApplication.instance()
        if app is None:
            return
        apply_theme(
            app, mode,
            compact=getattr(self, "_compact", False),
            accent=getattr(self, "_accent", None),
        )
        for widget in app.allWidgets():
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

    def clear_image_cache(self) -> None:
        """Delete cached covers and posters; they are re-fetched on demand."""
        import shutil

        root = platform_support.cache_dir()
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        self.toast.show_message("Image cache cleared.")

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self.isMaximized():
            self._settings.set_window_size(self.width(), self.height())
        self._settings.set_maximized(self.isMaximized())
        if self.manager is not None:
            self.manager.shutdown()
        super().closeEvent(event)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def resolved_theme(app: QApplication, mode: str = "system") -> str:
    scheme = app.styleHints().colorScheme()
    return theme.resolve(theme.normalize_mode(mode), system_dark=scheme == Qt.ColorScheme.Dark)


def apply_theme(
    app: QApplication,
    mode: str = "system",
    *,
    compact: bool = False,
    accent: str | None = None,
) -> str:
    resolved = resolved_theme(app, mode)
    set_theme(resolved, accent=accent)
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet(resolved, compact=compact, accent=accent))
    palette = app.palette()
    from PySide6.QtGui import QColor

    colours = theme.palette(resolved)
    if accent:
        colours = theme.with_accent(colours, accent)
    palette.setColor(palette.ColorRole.Window, QColor(colours["bg"]))
    palette.setColor(palette.ColorRole.WindowText, QColor(colours["text"]))
    palette.setColor(palette.ColorRole.Base, QColor(colours["surface"]))
    palette.setColor(palette.ColorRole.Text, QColor(colours["text"]))
    palette.setColor(palette.ColorRole.Highlight, QColor(colours["accent_fill"]))
    palette.setColor(palette.ColorRole.HighlightedText, QColor(colours["on_accent"]))
    app.setPalette(palette)
    return resolved


def _resolve_task_url(snapshot: TaskSnapshot) -> str | None:
    """Fresh URL for a restored task (the manager asks when it resumes one)."""
    try:
        return build_download_url(snapshot.identifier, snapshot.filename)
    except Exception:  # noqa: BLE001 - an unusable name just means "no URL"
        return None


def build_window(settings: Settings, download_dir: Path | None = None) -> Window:
    """Create the window and every long-lived object it needs."""
    # Pin the profile folder for the whole run: switching before a restart must
    # not let a later store open the other profile's files.
    profiles.start_session()
    download_dir = Path(download_dir or settings.download_dir
                        or platform_support.default_download_dir())
    # The active profile owns the library index; the Default profile's folder
    # is the one every existing install already uses.
    store = LibraryStore(profiles.active_file("library.json"), download_dir)
    client = ArchiveClient(cache_root=platform_support.cache_dir(),
                           timeout=float(settings.network_timeout))
    bridge_holder: dict[str, DownloadBridge] = {}
    manager = DownloadManager(
        observer=lambda: bridge_holder["b"].notify() if "b" in bridge_holder else None,
        queue_path=platform_support.data_dir() / "downloads.json",
        url_resolver=_resolve_task_url,
        timeout=float(settings.network_timeout),
        retry_limit=settings.download_retries,
    )
    window = Window(store, client, manager, download_dir, settings)
    bridge_holder["b"] = window.bridge
    return window


def self_test(window: "Window", app: QApplication) -> int:
    """Open every page and the Settings sheet without a human, then exit.

    The Windows smoke test used to prove only that the exe stayed running.  This
    walks the pages the owner would open and fails with a non-zero exit code on
    the first exception.  Settings is constructed directly because
    ``show_settings`` opens a modal dialog that would never return here.
    """
    problems: list[str] = []
    window.show()
    app.processEvents()
    for name in ("media", "downloads", "library", "browse"):
        try:
            window.show_view(name)
            app.processEvents()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            problems.append(f"{name}: {exc}")
    try:
        dialog = SettingsDialog(window._settings, window)  # noqa: SLF001
        dialog.show_tab("general")
        app.processEvents()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"settings: {exc}")
    for problem in problems:
        print(f"self-test failed: {problem}", file=sys.stderr)
    if not problems:
        print("self-test ok: browse, library, downloads, media, settings")
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv)
    app = QApplication(args)
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    platform_support.ensure_app_dirs()
    profiles.start_session()
    settings = Settings(profiles.active_settings_path())
    apply_theme(app, settings.theme)
    window = build_window(settings)
    if "--self-test" in args:
        return self_test(window, app)
    window.show()
    return app.exec()


#: The old name is kept so existing callers and tests keep working.
MainWindow = Window


if __name__ == "__main__":
    sys.exit(main())
