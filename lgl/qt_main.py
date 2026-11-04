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
from typing import Sequence

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, magnet as magnetlib, platform_support, theme
from .archive import ArchiveClient, build_download_url
from .catalog_store import CatalogStore
from .downloader import DownloadManager, TaskSnapshot
from .library_store import LibraryStore
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
    clear_layout,
    color,
    run_async,
    set_theme,
)
from .settings import Settings

APP_TITLE = "Lumora"
RAIL_WIDTH = 72
HEADER_HEIGHT = 64


class Window(QMainWindow):
    """The main window: rail + header + one page per destination."""

    def __init__(self, store: LibraryStore | None = None,
                 client: ArchiveClient | None = None,
                 manager: DownloadManager | None = None,
                 download_dir: Path | None = None,
                 settings: Settings | None = None) -> None:
        super().__init__()
        self._settings = settings or Settings()
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
            client, cache_root=platform_support.cache_dir(), catalog=self.catalog
        )
        self.browse.download_requested.connect(self._download_requested)
        self.browse.files_download_requested.connect(self._files_download_requested)
        self.browse.catalog_download_requested.connect(self._catalog_download_requested)
        self.browse.count_changed.connect(self._browse_count_changed)
        self.browse.reload_catalog()
        self.stack.addWidget(self.browse)

        self.library = LibraryPage(store, self._settings, client, self.download_dir)
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
        self.stack.addWidget(self.media)

        self.toast = Toast(root)
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
        column = QVBoxLayout(rail)
        column.setContentsMargins(4, 10, 4, 10)
        column.setSpacing(2)
        self.rail_buttons: dict[str, RailButton] = {}
        for key, icon_name, text, tip in (
            ("browse", "grid", "Browse", "Browse the collection (Ctrl+1)"),
            ("library", "library", "Library", "Your downloaded games (Ctrl+2)"),
            ("downloads", "downloads", "Downloads", "The download queue (Ctrl+3)"),
            ("media", "media", "Media", "Movies & TV (Ctrl+4)"),
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
        pill.setFixedHeight(40)
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

        self.menu_btn = IconButton("menu", "Menu", 18, 34)
        self.menu_btn.clicked.connect(self._show_menu)
        row.addWidget(self.menu_btn)
        self.refresh_btn = IconButton("refresh", "Refresh (reload collection, rescan library)", 17, 34)
        self.refresh_btn.clicked.connect(self.refresh_all)
        row.addWidget(self.refresh_btn)
        return header

    def _install_shortcuts(self) -> None:
        for keys, callback in (
            ("Ctrl+1", lambda: self.show_view("browse")),
            ("Ctrl+2", lambda: self.show_view("library")),
            ("Ctrl+3", lambda: self.show_view("downloads")),
            ("Ctrl+4", lambda: self.show_view("media")),
            ("Ctrl+F", self.focus_search),
            ("Ctrl+K", self.focus_search),
            ("Ctrl+R", self.refresh_all),
            ("F5", self.refresh_all),
            ("Ctrl+,", lambda: self.show_settings()),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(callback)

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
        self.stack.setCurrentWidget(page)  # type: ignore[arg-type]
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
        self.stack.setCurrentWidget(page)
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
        total = getattr(self, "_browse_total", 0)
        return f"Search {total:,} games" if total else "Search games"

    def _sync_search_field(self, key: str) -> None:
        if key == "browse":
            self.search_field.setPlaceholderText(self._browse_placeholder())
            self.search_field.setEnabled(True)
        elif key == "library":
            self.search_field.setPlaceholderText("Search library")
            self.search_field.setEnabled(True)
        elif key == "media":
            self.search_field.setPlaceholderText("Search movies, TV, anime…")
            self.search_field.setEnabled(True)
        else:
            self.search_field.setPlaceholderText("Search")
            self.search_field.setEnabled(False)

    def focus_search(self) -> None:
        self.search_field.setFocus()
        self.search_field.selectAll()

    def _on_search_changed(self, text: str) -> None:
        current = getattr(self, "_current", "library")
        if current == "browse":
            self.browse.refresh()
        elif current == "library":
            self.library.search.setText(text)
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

    # menu / dialogs -------------------------------------------------------
    def _show_menu(self) -> None:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        sources = QAction("Website Sources…", self)
        sources.triggered.connect(self.show_sources)
        menu.addAction(sources)
        catalog = QAction("Kryoto Catalog…", self)
        catalog.triggered.connect(self.show_kryoto_catalog)
        menu.addAction(catalog)
        queue = QAction("Show Download Queue", self)
        queue.triggered.connect(lambda: self.show_view("downloads"))
        menu.addAction(queue)
        maintenance = menu.addMenu("Maintenance")
        rescan = maintenance.addAction("Rescan library")
        rescan.triggered.connect(self.library.rescan)
        reload_action = maintenance.addAction("Reload collection")
        reload_action.triggered.connect(lambda: self.browse.load(force=True))
        catalog_downloads = maintenance.addAction("Download from catalogue…")
        catalog_downloads.triggered.connect(self.show_catalog_downloads)
        artwork_action = maintenance.addAction("Sync catalogue artwork")
        artwork_action.triggered.connect(lambda: self.sync_artwork(False))
        recheck_action = maintenance.addAction("Re-check games with no artwork match")
        recheck_action.triggered.connect(lambda: self.sync_artwork(True))
        cache_action = maintenance.addAction("Clear image cache")
        cache_action.triggered.connect(self.clear_image_cache)
        open_data = maintenance.addAction("Open data folder")
        open_data.triggered.connect(
            lambda: platform_support.open_folder(platform_support.data_dir())
        )
        folder = QAction("Open download folder", self)
        folder.triggered.connect(lambda: platform_support.open_folder(self.download_dir))
        menu.addAction(folder)
        menu.addSeparator()
        updates = QAction("Check for updates…", self)
        updates.triggered.connect(self.check_updates)
        menu.addAction(updates)
        settings = QAction("Settings…", self)
        settings.triggered.connect(self.show_settings)
        menu.addAction(settings)
        about = QAction(f"About {APP_TITLE}", self)
        about.triggered.connect(self.show_about)
        menu.addAction(about)
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

    def show_settings(self, tab: str = "general") -> None:
        dialog = SettingsDialog(self._settings, self)
        dialog.show_tab(tab)
        dialog.exec()

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
        apply_theme(app, mode)
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


def apply_theme(app: QApplication, mode: str = "system") -> str:
    resolved = resolved_theme(app, mode)
    set_theme(resolved)
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet(resolved))
    palette = app.palette()
    from PySide6.QtGui import QColor

    colours = theme.palette(resolved)
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
    download_dir = Path(download_dir or settings.download_dir
                        or platform_support.default_download_dir())
    store = LibraryStore(platform_support.data_dir() / "library.json", download_dir)
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


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv if argv is not None else sys.argv))
    app.setApplicationName(APP_TITLE)
    app.setApplicationDisplayName(APP_TITLE)
    platform_support.ensure_app_dirs()
    settings = Settings()
    apply_theme(app, settings.theme)
    window = build_window(settings)
    window.show()
    return app.exec()


#: The old name is kept so existing callers and tests keep working.
MainWindow = Window


if __name__ == "__main__":
    sys.exit(main())
