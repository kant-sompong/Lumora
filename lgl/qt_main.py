"""Qt 6 (PySide6) frontend for Native Game Library - the Windows UI.

It mirrors the GTK frontend (``ui.py``): the same header bar with centred
Archive / Library tabs, the same toolbar, poster-grid tiles and right-hand
detail panel.  All colours, radii and type sizes come from ``theme.py`` through
``qt_theme`` so the two frontends stay in step.

Only the platform-neutral core is used for data (``LibraryStore``, ``sorting``,
``platform_support``); nothing here imports GTK.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, platform_support, sorting, theme
from .archive import ArchiveClient
from .library_store import LibraryEntry, LibraryStore
from .qt_theme import build_stylesheet
from .qt_archive import ArchiveView
from .qt_widgets import THEME_STATE, CoverArt, FlowLayout, clear_layout, search_icon
from .util import human_size

APP_TITLE = "Native Game Library"
TILE_W = 222
COVER_H = 180


# --------------------------------------------------------------------------
# small building blocks
# --------------------------------------------------------------------------


class Tile(QFrame):
    """One library game: cover, title, size/year line, compatibility line."""

    clicked = Signal(str)
    favorite_toggled = Signal(str)

    def __init__(self, entry: LibraryEntry) -> None:
        super().__init__()
        self.setObjectName("tile")
        self.identifier = entry.identifier
        self.setFixedWidth(TILE_W + 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("hover", False)
        self.setProperty("selected", False)

        col = QVBoxLayout(self)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(2)

        self.cover = CoverArt(TILE_W, COVER_H)
        self.cover.set_image(entry.cover_path)
        col.addWidget(self.cover)

        self.star = QToolButton(self.cover)
        self.star.setObjectName("favorite-star")
        self.star.setCheckable(True)
        self.star.setChecked(entry.favorite)
        self.star.setText("★" if entry.favorite else "☆")
        self.star.setFixedSize(22, 22)
        self.star.move(TILE_W - 28, 6)
        self.star.setVisible(entry.favorite)
        self.star.toggled.connect(
            lambda on: self.star.setText("★" if on else "☆")
        )
        self.star.clicked.connect(lambda: self.favorite_toggled.emit(self.identifier))

        title = QLabel(entry.title or entry.identifier)
        title.setObjectName("tile-title")
        title.setWordWrap(True)
        title.setContentsMargins(2, 7, 0, 0)
        col.addWidget(title)

        bits = [b for b in (entry.year, human_size(entry.size) if entry.size else "") if b]
        meta = QLabel(" · ".join(bits) or "—")
        meta.setObjectName("tile-meta")
        meta.setContentsMargins(2, 0, 0, 0)
        col.addWidget(meta)

        if entry.compatibility:
            compat = QLabel(entry.compatibility)
            compat.setObjectName("tile-compat")
            compat.setContentsMargins(2, 0, 0, 0)
            col.addWidget(compat)
        col.addStretch(1)

    def set_selected(self, on: bool) -> None:
        self._flag("selected", on)

    def _flag(self, name: str, on: bool) -> None:
        self.setProperty(name, on)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._flag("hover", True)
        self.star.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._flag("hover", False)
        if not (self.star.isChecked() or self.property("selected")):
            self.star.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.identifier)
        super().mousePressEvent(event)


# --------------------------------------------------------------------------
# detail panel
# --------------------------------------------------------------------------


class DetailPanel(QFrame):
    """Right-hand "Selected game" panel."""

    closed = Signal()
    action = Signal(str, str)  # (action name, identifier)

    ROWS = (
        ("STATUS", "status"),
        ("COMPATIBILITY", "compatibility"),
        ("SIZE", "size"),
        ("FILES", "files"),
        ("INTEGRITY", "integrity"),
    )
    ABOUT = (
        ("DEVELOPER", "developer"),
        ("PUBLISHER", "publisher"),
        ("RELEASED", "release_date"),
        ("VERSION", "version"),
        ("GENRES", "genres"),
        ("PLATFORMS", "platforms"),
        ("SOURCE", "metadata_provider"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("detail")
        self.setFixedWidth(460)
        self._identifier = ""
        self._vals: dict[str, QLabel] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(6)

        head = QHBoxLayout()
        eyebrow = QLabel("SELECTED GAME")
        eyebrow.setObjectName("detail-eyebrow")
        head.addWidget(eyebrow)
        head.addStretch(1)
        close = QToolButton()
        close.setObjectName("flat")
        close.setText("✕")
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        col.addLayout(head)

        self.title = QLabel()
        self.title.setObjectName("detail-title")
        self.title.setWordWrap(True)
        self.title.setContentsMargins(0, 8, 0, 0)
        col.addWidget(self.title)
        self.sub = QLabel()
        self.sub.setObjectName("detail-sub")
        col.addWidget(self.sub)
        col.addSpacing(10)

        for label, key in self.ROWS:
            col.addLayout(self._row(label, key))
        col.addSpacing(8)
        about = QLabel("ABOUT")
        about.setObjectName("kv-key")
        col.addWidget(about)
        for label, key in self.ABOUT:
            col.addLayout(self._row(label, key))

        self.note = QLabel()
        self.note.setObjectName("detail-note")
        self.note.setWordWrap(True)
        self.note.setContentsMargins(0, 8, 0, 0)
        col.addWidget(self.note)
        col.addSpacing(10)

        grid_rows = (
            (("Open folder", "open_folder"), ("Show files", "show_files")),
            (("Edit details…", "edit"),),
            (("Verify integrity", "verify"), ("☆ Favorite", "favorite")),
            (("Review && launch…", "launch"),),
        )
        for row in grid_rows:
            line = QHBoxLayout()
            line.setSpacing(8)
            for text, name in row:
                btn = QPushButton(text)
                if name == "launch":
                    btn.setObjectName("suggested")
                btn.clicked.connect(lambda _=False, n=name: self.action.emit(n, self._identifier))
                line.addWidget(btn)
            col.addLayout(line)
        col.addStretch(1)

    def _row(self, label: str, key: str) -> QHBoxLayout:
        line = QHBoxLayout()
        k = QLabel(label)
        k.setObjectName("kv-key")
        k.setFixedWidth(96)
        v = QLabel("—")
        v.setObjectName("kv-val")
        v.setWordWrap(True)
        line.addWidget(k, 0, Qt.AlignTop)
        line.addWidget(v, 1)
        self._vals[key] = v
        return line

    def show_entry(self, entry: LibraryEntry) -> None:
        self._identifier = entry.identifier
        self.title.setText(entry.title or entry.identifier)
        who = " · ".join(b for b in (entry.creator, entry.year) if b)
        self.sub.setText(who or "No creator or year recorded")

        def put(key: str, text: str, kind: str = "kv-val") -> None:
            label = self._vals[key]
            label.setText(text or "—")
            label.setObjectName(kind)
            label.style().unpolish(label)
            label.style().polish(label)

        put("status", (entry.status or "").replace("_", " ").title())
        put("compatibility", entry.compatibility)
        put("size", human_size(entry.size) if entry.size else "")
        put("files", f"{entry.file_count} files" if entry.file_count else "")
        put("integrity", entry.integrity or "No published checksum",
            "kv-val" if entry.integrity else "kv-warn")
        for _label, key in self.ABOUT:
            value = getattr(entry, key, "")
            if isinstance(value, (tuple, list)):
                value = ", ".join(value)
            put(key, str(value or ""))
        self.note.setText(
            "" if entry.description
            else "No extra metadata yet. Use “Edit details…” to add some; "
            "catalog refreshes never overwrite fields you typed yourself."
        )


# --------------------------------------------------------------------------
# library view
# --------------------------------------------------------------------------


class LibraryView(QWidget):
    """Toolbar + poster grid + optional detail panel."""

    def __init__(self, store: LibraryStore | None = None) -> None:
        super().__init__()
        self.store = store
        self._entries: list[LibraryEntry] = []
        self._tiles: dict[str, Tile] = {}
        self._selected = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QVBoxLayout()
        left.setSpacing(0)
        root.addLayout(left, 1)

        bar = QFrame()
        bar.setObjectName("toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 4)
        row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search library")
        self.search.addAction(search_icon(QColor(THEME_STATE["muted"])), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.view_toggle = QPushButton("☰")
        self.view_toggle.setToolTip("Grid / list")
        row.addWidget(self.view_toggle)
        self.filters = QPushButton("Filters")
        row.addWidget(self.filters)
        self.sort = QComboBox()
        for key, spec in sorting.SORTS.items():
            if key != "relevance":
                self.sort.addItem(f"Sort: {spec.label}", key)
        self.sort.setCurrentIndex(self.sort.findData("name"))
        self.sort.currentIndexChanged.connect(self.refresh)
        self.sort.setMinimumWidth(180)
        row.addWidget(self.sort)
        rescan = QPushButton("↻")
        rescan.setToolTip("Rescan library")
        rescan.clicked.connect(self.rescan)
        row.addWidget(rescan)
        more = QToolButton()
        more.setText("⋮")
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        menu.addAction("Find duplicates…")
        menu.addAction("Export library…")
        menu.addAction("Import library…")
        more.setMenu(menu)
        row.addWidget(more)
        left.addWidget(bar)

        self.usage = QLabel()
        self.usage.setObjectName("usage")
        self.usage.setContentsMargins(26, 0, 0, 6)
        left.addWidget(self.usage)
        line = QFrame()
        line.setObjectName("metabar")
        line.setFixedHeight(1)
        left.addWidget(line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_host = QWidget()
        self.flow = FlowLayout(self.grid_host, spacing=8)
        self.flow.setContentsMargins(10, 16, 10, 24)
        self.scroll.setWidget(self.grid_host)
        left.addWidget(self.scroll, 1)

        self.empty = QLabel("No games yet\nDownload something from the Archive to see it here.")
        self.empty.setObjectName("empty-sub")
        self.empty.setAlignment(Qt.AlignCenter)
        left.addWidget(self.empty, 1)

        self.detail = DetailPanel()
        self.detail.hide()
        self.detail.closed.connect(self.clear_selection)
        root.addWidget(self.detail)

    # data ------------------------------------------------------------------
    def set_entries(self, entries: Sequence[LibraryEntry]) -> None:
        self._entries = list(entries)
        self.refresh()

    def rescan(self) -> None:
        if self.store is not None:
            self.set_entries(self.store.entries())

    def refresh(self) -> None:
        query = self.search.text().strip().lower()
        shown = [
            e for e in self._entries
            if not query or query in (e.title or "").lower() or query in e.identifier.lower()
        ]
        shown = sorting.sort_entries(shown, self.sort.currentData() or "name")
        clear_layout(self.flow)
        self._tiles.clear()
        for entry in shown:
            tile = Tile(entry)
            tile.clicked.connect(self.select)
            tile.favorite_toggled.connect(self._toggle_favorite)
            self._tiles[entry.identifier] = tile
            self.flow.addWidget(tile)
        total = sum(e.size for e in self._entries)
        self.usage.setText(
            f"{len(shown)} shown · {len(self._entries)} games · {human_size(total)}"
        )
        self.scroll.setVisible(bool(shown))
        self.empty.setVisible(not shown)
        if self._selected in self._tiles:
            self._tiles[self._selected].set_selected(True)

    # selection -------------------------------------------------------------
    def select(self, identifier: str) -> None:
        if self._selected in self._tiles:
            self._tiles[self._selected].set_selected(False)
        self._selected = identifier
        if identifier in self._tiles:
            self._tiles[identifier].set_selected(True)
        entry = next((e for e in self._entries if e.identifier == identifier), None)
        if entry:
            self.detail.show_entry(entry)
            self.detail.show()

    def clear_selection(self) -> None:
        if self._selected in self._tiles:
            self._tiles[self._selected].set_selected(False)
        self._selected = ""
        self.detail.hide()

    def _toggle_favorite(self, identifier: str) -> None:
        if self.store is not None:
            self.store.toggle_favorite(identifier)


# --------------------------------------------------------------------------
# main window
# --------------------------------------------------------------------------


class Placeholder(QWidget):
    """Stand-in for views that are ported in later steps."""

    def __init__(self, title: str, sub: str) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.addStretch(1)
        for text, name in ((title, "empty-title"), (sub, "empty-sub")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setAlignment(Qt.AlignCenter)
            col.addWidget(label)
        col.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, store: LibraryStore | None = None,
                 client: ArchiveClient | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)

        host = QWidget()
        host.setObjectName("root")
        self.setCentralWidget(host)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QFrame()
        header.setObjectName("headerbar")
        header.setFixedHeight(48)
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 0, 12, 0)
        h.addStretch(1)
        self.tabs: dict[str, QPushButton] = {}
        for name in ("Archive", "Library"):
            btn = QPushButton(name)
            btn.setObjectName("viewtab")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, n=name: self.show_view(n))
            self.tabs[name] = btn
            h.addWidget(btn)
        h.addStretch(1)
        refresh = QToolButton()
        refresh.setObjectName("flat")
        refresh.setText("↻")
        h.addWidget(refresh)
        self.downloads_btn = QPushButton("↓  Downloads")
        h.addWidget(self.downloads_btn)
        menu_btn = QToolButton()
        menu_btn.setText("☰")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(menu_btn)
        for text in ("Settings", "Keyboard shortcuts", f"About {APP_TITLE} {__version__}"):
            menu.addAction(text)
        menu_btn.setMenu(menu)
        h.addWidget(menu_btn)
        col.addWidget(header)

        self.stack = QStackedWidget()
        self.library = LibraryView(store)
        self.archive = ArchiveView(client)
        self.stack.addWidget(self.archive)
        self.stack.addWidget(self.library)
        col.addWidget(self.stack, 1)
        self.show_view("Library")

    def show_view(self, name: str) -> None:
        for key, btn in self.tabs.items():
            btn.setChecked(key == name)
        self.stack.setCurrentIndex(0 if name == "Archive" else 1)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def resolved_theme(app: QApplication, mode: str = "system") -> str:
    scheme = app.styleHints().colorScheme()
    return theme.resolve(theme.normalize_mode(mode), system_dark=scheme == Qt.ColorScheme.Dark)


def apply_theme(app: QApplication, mode: str = "system") -> str:
    resolved = resolved_theme(app, mode)
    THEME_STATE["muted"] = theme.palette(resolved)["muted"]
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet(resolved))
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv if argv is not None else sys.argv))
    app.setApplicationName(APP_TITLE)
    from .settings import Settings

    settings = Settings()
    apply_theme(app, settings.theme)
    download_dir = Path(settings.download_dir or platform_support.default_download_dir())
    store = LibraryStore(platform_support.data_dir() / "library.json", download_dir)
    client = ArchiveClient(cache_root=platform_support.cache_dir())
    window = MainWindow(store, client)
    window.library.set_entries(store.entries())
    window.archive.load()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
