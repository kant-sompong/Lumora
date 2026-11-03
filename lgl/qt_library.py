"""Library page for the Qt (Windows) frontend.

Mirrors the GTK Library view: a search field with the sort cluster, filter and
overflow controls, the stats line, a grid (or list) of installed games, and the
same right-hand "Selected game" panel with metadata, launch candidates and
actions.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import external_dm, integrity, platform_support
from .archive import ArchiveClient, ArchiveItem
from .launch import open_launch_log
from .library import MANIFEST_NAME, scan_game_folder
from .library_store import LibraryEntry, LibraryStore, SORT_DEFAULT_DESCENDING, STATUSES
from .qt_icons import icon, paint_icon
from .qt_widgets import (
    CoverArt,
    CoverGrid,
    DirectionButton,
    ElideLabel,
    EmptyState,
    IconButton,
    Popover,
    SortPill,
    Toast,
    ViewToggle,
    cached_image_bytes,
    clear_layout,
    label,
    pixmap_from_bytes,
    run_async,
    scroll_page,
    search_icon,
    space,
)
from .util import human_size, is_within

#: Sort choices in the order the GTK view offers them.
LIBRARY_SORTS = (
    ("recent", "Recently seen"),
    ("added", "Recently added"),
    ("played", "Recently played"),
    ("year", "Release year"),
    ("size", "Installed size"),
    ("developer", "Developer"),
    ("genre", "Genre"),
    ("title", "Title"),
)
STATUS_LABELS = {
    "all": "All statuses",
    "downloaded": "Downloaded",
    "installed": "Installed",
    "incomplete": "Incomplete",
    "failed": "Failed",
    "missing": "Missing",
}
PLATFORM_LABELS = (
    ("all", "All platforms"),
    ("Native Linux", "Native Linux"),
    ("Windows", "Windows"),
    ("DOS", "DOS"),
    ("Java", "Java"),
    ("Source only", "Source only"),
    ("Unknown", "Unknown"),
)

#: Files that are never what you start a game with.
_HELPER_HINTS = (
    "unins", "setup", "install", "redist", "vcredist", "dxsetup", "dotnet",
    "crashhandler", "crashpad", "updater", "update", "patch", "readme",
)
_LAUNCH_SUFFIXES = (".exe", ".com", ".bat", ".cmd")


def _compact(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def launch_candidates(folder: str | Path, title: str, limit: int = 40) -> list[Path]:
    """Plausible start files in a game folder, best guess first.

    Only Windows-launchable files are offered; helper programs (uninstallers,
    installers, redistributables, updaters) are skipped, and files named after
    the game rank first.
    """
    root = Path(folder)
    if not root.is_dir():
        return []
    compact_title = _compact(title)
    found: list[tuple[int, str, Path]] = []
    try:
        for path in sorted(root.rglob("*"), key=lambda p: str(p).casefold()):
            if len(found) >= limit * 3:
                break
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in _LAUNCH_SUFFIXES:
                continue
            if path.name.casefold() == MANIFEST_NAME:
                continue
            stem = path.stem.casefold()
            if any(hint in stem for hint in _HELPER_HINTS):
                continue
            rank = 30
            if compact_title and len(compact_title) >= 4 and compact_title in _compact(stem):
                rank = 0
            elif any(word in stem for word in ("start", "run", "launch", "play", "game")):
                rank = 8
            depth = len(path.relative_to(root).parts) - 1
            found.append((rank + depth, str(path).casefold(), path))
    except OSError:
        return []
    found.sort()
    return [path for _rank, _key, path in found[:limit]]


def _elided(text: str, width: int = 300) -> str:
    return text if len(text) <= 90 else text[:90] + "…"


#: Gap between library cards.  Wide gutters left a shelf looking sparse, so
#: the cards sit closer together and the cells fill the row.
GRID_GAP = 12
GRID_GAP_VERTICAL = 14
#: Narrowest card the grid will place in a row.
GRID_MIN_CELL = 232


# --------------------------------------------------------------------------
# cards
# --------------------------------------------------------------------------


class LibraryCard(QFrame):
    """Square cover card for an installed game."""

    clicked = Signal(str)

    def __init__(self, entry: LibraryEntry) -> None:
        super().__init__()
        self.setObjectName("coverCard")
        self.setProperty("hover", False)
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.entry = entry
        self.identifier = entry.identifier
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        name = entry.title or entry.identifier
        self.cover = CoverArt(radius=6, crop=True, title=name)
        self.cover.setMinimumHeight(160)
        column.addWidget(self.cover)

        row = QHBoxLayout()
        row.setContentsMargins(2, 9, 2, 0)
        row.setSpacing(4)
        self.title = ElideLabel(name)
        self.title.setObjectName("coverTitle")
        row.addWidget(self.title, 1)
        self.star = QLabel("★" if entry.favorite else "")
        self.star.setStyleSheet("color: #b8863b;")
        row.addWidget(self.star, 0, Qt.AlignTop)
        column.addLayout(row)

        bits = [b for b in (entry.year, human_size(entry.size) if entry.size else "") if b]
        self.meta = ElideLabel(" · ".join(bits) or "—")
        self.meta.setObjectName("coverMeta")
        self.meta.setContentsMargins(2, 1, 2, 0)
        column.addWidget(self.meta)
        if entry.compatibility:
            compat = ElideLabel(entry.compatibility)
            compat.setObjectName("coverMeta")
            compat.setContentsMargins(2, 0, 2, 0)
            column.addWidget(compat)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.cover.setFixedHeight(max(140, self.width()))

    def set_selected(self, on: bool) -> None:
        self._flag("selected", on)

    def _flag(self, name: str, on: bool) -> None:
        self.setProperty(name, on)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._flag("hover", True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._flag("hover", False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.identifier)
        super().mouseReleaseEvent(event)


class LibraryRow(QFrame):
    """Compact list line for the library."""

    clicked = Signal(str)

    def __init__(self, entry: LibraryEntry) -> None:
        super().__init__()
        self.setObjectName("listRow")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.identifier = entry.identifier
        self.entry = entry
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 9, 4, 9)
        column = QVBoxLayout()
        column.setSpacing(2)
        name = ElideLabel(entry.title or entry.identifier)
        name.setObjectName("listTitle")
        column.addWidget(name)
        bits = [b for b in (entry.year, human_size(entry.size) if entry.size else "",
                            entry.compatibility, entry.status.replace("_", " ").title()) if b]
        meta = ElideLabel(" · ".join(bits))
        meta.setObjectName("listMeta")
        column.addWidget(meta)
        row.addLayout(column, 1)
        if entry.favorite:
            star = QLabel("★")
            star.setStyleSheet("color: #b8863b;")
            row.addWidget(star)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.identifier)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------
# dialogs
# --------------------------------------------------------------------------


class LaunchDialog(QDialog):
    """Review a downloaded file before starting it."""

    def __init__(self, path: Path, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Review downloaded file")
        self.path = path
        self.setMinimumWidth(520)
        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(10)

        column.addWidget(label("Review before launching", "pageTitle"))
        warning = label(
            "This file is third-party software. Running it may modify files or start "
            "other programs. Only launch it if you reviewed it and trust its source.",
            "detailNote", wrap=True,
        )
        column.addWidget(warning)

        card = QFrame()
        card.setStyleSheet("QFrame { border: 1px solid rgba(232,234,237,0.09); border-radius: 8px; }")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for index, (key, value) in enumerate((
            ("Game", title),
            ("File", path.name),
            ("Size", human_size(path.stat().st_size) if path.is_file() else "unknown"),
            ("Location", str(path)),
        )):
            grid.addWidget(label(key.upper(), "kvKey"), index, 0, Qt.AlignTop)
            grid.addWidget(label(value, "detailNote", wrap=True), index, 1)
        column.addWidget(card)

        self.confirm = QCheckBox("I have reviewed this file and trust it.")
        column.addWidget(self.confirm)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.launch = QPushButton("Launch")
        self.launch.setObjectName("suggested")
        self.launch.setEnabled(False)
        self.launch.clicked.connect(self.accept)
        row.addWidget(self.launch)
        column.addLayout(row)
        self.confirm.toggled.connect(self.launch.setEnabled)


class EditDetailsDialog(QDialog):
    """Hand-typed metadata; user edits are protected from catalog refreshes."""

    FIELDS = (
        ("title", "Title"),
        ("creator", "Creator"),
        ("year", "Release year"),
        ("developer", "Developer"),
        ("publisher", "Publisher"),
        ("version", "Version"),
    )

    def __init__(self, entry: LibraryEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Edit game details")
        self.setMinimumWidth(520)
        self._edits: dict[str, QLineEdit] = {}
        self._genres: QLineEdit
        self._description: QLineEdit
        self._notes: QLineEdit

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 20, 22, 18)
        column.setSpacing(8)
        column.addWidget(label(entry.title or entry.identifier, "pageTitle"))
        column.addWidget(label(
            "Values you type here are marked as yours and are never overwritten by "
            "metadata refreshes.", "detailNote", wrap=True,
        ))
        column.addSpacing(6)
        for key, text in self.FIELDS:
            column.addWidget(label(text, "kvKey"))
            field = QLineEdit(str(getattr(entry, key, "") or ""))
            field.setPlaceholderText(text)
            column.addWidget(field)
            self._edits[key] = field

        column.addWidget(label("Genres", "kvKey"))
        self._genres = QLineEdit(", ".join(entry.genres))
        self._genres.setPlaceholderText("Action, Puzzle")
        column.addWidget(self._genres)

        column.addWidget(label("Description", "kvKey"))
        self._description = QLineEdit(entry.description)
        self._description.setPlaceholderText("What this game is")
        column.addWidget(self._description)

        column.addWidget(label("Notes", "kvKey"))
        self._notes = QLineEdit(entry.notes)
        self._notes.setPlaceholderText("Private notes, stored in library.json")
        column.addWidget(self._notes)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton("Save")
        save.setObjectName("suggested")
        save.clicked.connect(self.accept)
        row.addWidget(save)
        column.addSpacing(8)
        column.addLayout(row)

    def values(self) -> dict[str, object]:
        data: dict[str, object] = {key: field.text().strip() for key, field in self._edits.items()}
        data["genres"] = [part.strip() for part in self._genres.text().split(",") if part.strip()]
        data["description"] = self._description.text().strip()
        data["notes"] = self._notes.text().strip()
        return data


class DuplicatesDialog(QDialog):
    def __init__(self, groups: Sequence[object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Duplicate files")
        self.resize(640, 480)
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 18, 20, 16)
        column.addWidget(label("Duplicate files", "pageTitle"))
        column.addWidget(label(
            "Files with the same name and size. Nothing is deleted here.", "detailNote"
        ))
        listing = QListWidget()
        for group in groups:
            kind = getattr(group, "kind", "")
            size = human_size(getattr(group, "size", 0))
            listing.addItem(f"{kind} · {size}")
            for path in getattr(group, "paths", []):
                listing.addItem(f"    {path}")
        if not groups:
            listing.addItem("No duplicate files were found.")
        column.addWidget(listing, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        column.addLayout(row)


# --------------------------------------------------------------------------
# detail panel
# --------------------------------------------------------------------------


class LibraryDetail(QFrame):
    """Right-hand "Selected game" panel, matching the GTK layout."""

    closed = Signal()
    action = Signal(str, str)  # action name, identifier

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
        self.setFixedWidth(440)
        self._identifier = ""
        self._entry: LibraryEntry | None = None
        self._candidates: list[Path] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, _body, column = scroll_page()
        outer.addWidget(scroll)
        column.setContentsMargins(20, 16, 20, 18)
        column.setSpacing(6)
        self._column = column

        head = QHBoxLayout()
        head.addWidget(label("SELECTED GAME", "detailEyebrow"))
        head.addStretch(1)
        close = IconButton("close", "Close", 14, 30)
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        column.addLayout(head)

        self.title = label("", "detailTitle", wrap=True)
        self.title.setContentsMargins(0, 10, 0, 0)
        column.addWidget(self.title)
        self.sub = label("", "detailSub", wrap=True)
        column.addWidget(self.sub)
        column.addSpacing(10)

        self._vals: dict[str, QLabel] = {}
        for text, key in self.ROWS:
            column.addLayout(self._row(text, key))
        column.addSpacing(10)
        column.addWidget(label("ABOUT", "kvKey"))
        for text, key in self.ABOUT:
            column.addLayout(self._row(text, key))

        self.note = label("", "detailNote", wrap=True)
        self.note.setContentsMargins(0, 10, 0, 0)
        column.addWidget(self.note)

        self.compat_note = label("", "detailNote", wrap=True)
        column.addWidget(self.compat_note)
        column.addSpacing(10)

        self.launch_head = label("START WITH", "kvKey")
        column.addWidget(self.launch_head)
        self.launch_host = QWidget()
        self._launch_column = QVBoxLayout(self.launch_host)
        self._launch_column.setContentsMargins(0, 0, 0, 0)
        self._launch_column.setSpacing(2)
        column.addWidget(self.launch_host)
        self.launch_note = label("", "detailNote", wrap=True)
        column.addWidget(self.launch_note)
        # The actions follow the details.  A stretch here pushed every button to
        # the bottom of the panel and left a tall empty band above them.
        column.addSpacing(12)

        for row_spec in (
            (("Open folder", "open_folder"), ("Show files", "show_files")),
            (("Edit details…", "edit"),),
            (("Verify integrity", "verify"), ("☆ Favorite", "favorite")),
            (("Review && launch…", "launch"),),
            (("Add magnet…", "magnet"),),
            (("Send to JDownloader", "jd"), ("Send to AB Download", "ab")),
            (("Remove from library", "remove"),),
        ):
            line = QHBoxLayout()
            line.setSpacing(8)
            for text, name in row_spec:
                btn = QPushButton(text.replace("&&", "&"))
                if name == "launch":
                    btn.setObjectName("suggested")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setMinimumHeight(32)
                btn.clicked.connect(
                    lambda _=False, action=name: self.action.emit(action, self._identifier)
                )
                line.addWidget(btn)
                if name == "favorite":
                    self.favorite_btn = btn
            column.addLayout(line)
        column.addStretch(1)

    def _row(self, text: str, key: str) -> QHBoxLayout:
        line = QHBoxLayout()
        line.setSpacing(10)
        head = label(text, "kvKey")
        head.setFixedWidth(108)
        line.addWidget(head, 0, Qt.AlignTop)
        value = label("—", "kvVal", wrap=True)
        line.addWidget(value, 1)
        self._vals[key] = value
        return line

    def _put(self, key: str, text: str, kind: str = "kvVal") -> None:
        value = self._vals[key]
        value.setText(text or "—")
        value.setObjectName(kind)
        value.style().unpolish(value)
        value.style().polish(value)

    def show_entry(self, entry: LibraryEntry, cover: QPixmap | None = None) -> None:
        self._identifier = entry.identifier
        self._entry = entry
        self.title.setText(entry.title or entry.identifier)
        self.sub.setText(
            " · ".join(b for b in (entry.creator, entry.year) if b)
            or "No creator or year recorded"
        )
        self._put("status", (entry.status or "").replace("_", " ").title())
        self._put("compatibility", entry.compatibility)
        self._put("size", human_size(entry.size) if entry.size else "")
        self._put("files", f"{entry.file_count} files" if entry.file_count else "")
        self._put(
            "integrity", entry.integrity or "No published checksum",
            "kvVal" if entry.integrity else "kvWarn",
        )
        for _text, key in self.ABOUT:
            value = getattr(entry, key, "")
            if isinstance(value, (tuple, list)):
                value = ", ".join(value)
            self._put(key, str(value or ""))
        self.note.setText(
            entry.description or entry.notes or
            "No extra metadata yet. Use “Edit details…” to add some; catalog refreshes "
            "never overwrite fields you typed yourself."
        )
        self.compat_note.setText(
            f"Detected platform: {entry.compatibility}. This is a best-effort guess "
            "from file names and headers; no file was executed."
            if entry.compatibility else ""
        )
        self.favorite_btn.setText("★ Favorite" if entry.favorite else "☆ Favorite")
        self._load_candidates(entry)

    def _load_candidates(self, entry: LibraryEntry) -> None:
        clear_layout(self._launch_column)
        self._candidates = []
        folder = Path(entry.folder) if entry.folder else None
        if folder is None or not folder.is_dir():
            self.launch_note.setText("The game folder is missing, so nothing can be started.")
            return
        self.launch_note.setText("Looking for start files…")

        def done(result: object) -> None:
            if self._identifier != entry.identifier:
                return
            paths = list(result) if isinstance(result, list) else []
            self._candidates = paths
            clear_layout(self._launch_column)
            if not paths:
                self.launch_note.setText(
                    "No .exe, .bat or .cmd file was found in this folder. Open the folder "
                    "to review its contents yourself."
                )
                return
            self.launch_note.setText(
                "The chosen file is reviewed before anything runs."
            )
            for index, path in enumerate(paths[:6]):
                radio = QRadioButton(f"{path.name}")
                radio.setProperty("path", str(path))
                radio.setChecked(index == 0)
                self._launch_column.addWidget(radio)
                self._candidates.append(path)

        run_async(lambda: launch_candidates(folder, entry.title), done)

    def chosen_path(self) -> Path | None:
        for index in range(self._launch_column.count()):
            widget = self._launch_column.itemAt(index).widget()
            if isinstance(widget, QRadioButton) and widget.isChecked():
                value = widget.property("path")
                return Path(str(value)) if value else None
        return self._candidates[0] if self._candidates else None


# --------------------------------------------------------------------------
# filter popover
# --------------------------------------------------------------------------


class LibraryFilters(Popover):
    """The ``Filter`` sheet: status/platform/genre plus display toggles."""

    changed = Signal()

    def __init__(self, page: "LibraryPage") -> None:
        super().__init__(page)
        self.setMinimumWidth(268)
        self._page = page
        column = self.column()
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(4)
        column.addWidget(label("Filters", "eyebrow"))
        column.addSpacing(4)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.status = QComboBox()
        for key, text in STATUS_LABELS.items():
            self.status.addItem(text, key)
        self.platform = QComboBox()
        for key, text in PLATFORM_LABELS:
            self.platform.addItem(text, key)
        self.genre = QComboBox()
        self.genre.addItem("All genres", "all")
        for name in page.genres():
            self.genre.addItem(name, name)
        for row, (text, widget) in enumerate((
            ("Status", self.status), ("Platform", self.platform), ("Genre", self.genre),
        )):
            grid.addWidget(label(text, "popoverTitle"), row, 0)
            grid.addWidget(widget, row, 1)
        column.addLayout(grid)

        for widget in (self.status, self.platform, self.genre):
            widget.currentIndexChanged.connect(self._emit)

        column.addSpacing(6)
        self.games_only = self._toggle("Show non-game folders", page.settings.library_games_only)
        self.show_missing = self._toggle(
            "Show missing folders", not page.settings.library_show_missing
        )
        self.group_copies = self._toggle("Group copies", page.settings.library_group_copies)
        self.hide_adult = self._toggle("Hide adult titles", page.settings.library_hide_adult)
        column.addSpacing(4)
        clear = QPushButton("Clear filters")
        clear.setObjectName("popoverAction")
        clear.clicked.connect(self.clear)
        column.addWidget(clear)

    def _toggle(self, text: str, value: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(bool(value))
        box.toggled.connect(self._emit)
        self.column().addWidget(box)
        return box

    def _emit(self) -> None:
        self.changed.emit()

    def clear(self) -> None:
        self.status.setCurrentIndex(0)
        self.platform.setCurrentIndex(0)
        self.genre.setCurrentIndex(0)
        self.games_only.setChecked(True)
        self.show_missing.setChecked(True)
        self.group_copies.setChecked(False)
        self.hide_adult.setChecked(False)
        self.changed.emit()


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


class LibraryPage(QWidget):
    """Installed games: search, sort, filter, grid/list and detail panel."""

    changed = Signal()
    magnet_requested = Signal(str)
    external_requested = Signal(str, str)  # manager key, identifier

    def __init__(self, store: LibraryStore | None, settings=None,
                 client: ArchiveClient | None = None,
                 download_dir: Path | None = None) -> None:
        super().__init__()
        self.store = store
        self.settings = settings
        self.client = client
        self.download_dir = Path(download_dir) if download_dir else Path.home()
        self._entries: list[LibraryEntry] = []
        self._shown: list[LibraryEntry] = []
        self._cards: dict[str, QWidget] = {}
        self._pix: dict[str, QPixmap] = {}
        self._selected = ""
        self._descending: bool | None = None
        self._sort_key = "size"
        self._view = "grid"
        self.popover: LibraryFilters | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QWidget()
        left.setObjectName("pageBody")
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        root.addWidget(left, 1)

        toolbar = QWidget()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(24, 16, 24, 10)
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("librarySearch")
        self.search.setPlaceholderText("Search library")
        self.search.addAction(search_icon(self.palette().color(self.foregroundRole())),
                              QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        self.search.setClearButtonEnabled(False)
        bar.addWidget(self.search, 1)

        self.view_toggle = ViewToggle("Grid / list")
        self.view_toggle.clicked.connect(self._toggle_view)
        self.view_toggle.set_list(False)
        bar.addWidget(self.view_toggle)

        self.sort = SortPill(self._sort_label(), "Sort the library by…")
        self.sort.clicked.connect(self._sort_menu)
        bar.addWidget(self.sort)
        self.direction = DirectionButton()
        self.direction.clicked.connect(self._flip)
        bar.addWidget(self.direction)
        self.filter_btn = QPushButton("Filter")
        self.filter_btn.setCursor(Qt.PointingHandCursor)
        self.filter_btn.clicked.connect(self._show_filters)
        bar.addWidget(self.filter_btn)
        self.refresh_btn = IconButton("refresh", "Rescan the library", 17, 34)
        self.refresh_btn.clicked.connect(self.rescan)
        bar.addWidget(self.refresh_btn)
        self.more = IconButton("menu", "More library actions", 17, 34)
        self.more.clicked.connect(self._more_menu)
        bar.addWidget(self.more)
        layout.addWidget(toolbar)

        self.stats = label("", "statLine")
        self.stats.setContentsMargins(30, 0, 24, 10)
        layout.addWidget(self.stats)

        self.scroll, self._body, column = scroll_page()
        # The grid's width changes when the detail panel opens or closes, and
        # that is a viewport resize, not a page resize: without this the cards
        # kept the old column count and the last one was clipped behind the
        # panel.
        self.scroll.viewport().installEventFilter(self)
        column.setContentsMargins(24, 4, 24, 20)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(GRID_GAP)
        self.grid.setVerticalSpacing(GRID_GAP_VERTICAL)
        column.addWidget(self.grid_host)
        self.list_host = QWidget()
        self.list_column = QVBoxLayout(self.list_host)
        self.list_column.setContentsMargins(0, 0, 0, 0)
        self.list_column.setSpacing(0)
        self.list_host.hide()
        column.addWidget(self.list_host)
        self.empty = EmptyState(
            "library", "No games yet",
            "Download something from Browse, or point Settings at a folder that already has games.",
        )
        column.addWidget(self.empty)
        column.addStretch(1)
        layout.addWidget(self.scroll, 1)

        self.detail = LibraryDetail()
        self.detail.hide()
        self.detail.closed.connect(self.clear_selection)
        self.detail.action.connect(self._on_action)
        root.addWidget(self.detail)

    # data ------------------------------------------------------------------
    def genres(self) -> list[str]:
        if self.store is not None:
            try:
                return self.store.genres()
            except Exception:  # noqa: BLE001 - a broken index must not break the menu
                return []
        return []

    def set_entries(self, entries: Sequence[LibraryEntry]) -> None:
        self._entries = list(entries)
        self.refresh()

    def rescan(self) -> None:
        if self.store is None:
            return
        store = self.store

        def work() -> object:
            report = store.scan(self.download_dir)
            return store.entries(), report

        def done(result: object) -> None:
            entries, report = result  # type: ignore[misc]
            self.set_entries(entries)
            added = getattr(report, "added", 0)
            updated = getattr(report, "updated", 0)
            message = f"Library rescanned: {added} added, {updated} updated."
            errors = getattr(report, "errors", [])
            if errors:
                message += f" {errors[0]}"
            self._toast(message, error=bool(errors))

        run_async(work, done, lambda exc: self._toast(f"Rescan failed: {exc}", error=True))

    def refresh(self) -> None:
        query = self.search.text().strip()
        entries = list(self._entries)
        settings = self.settings
        games_only = settings.library_games_only if settings is not None else True
        show_missing = settings.library_show_missing if settings is not None else True
        hide_adult = settings.library_hide_adult if settings is not None else False
        group = settings.library_group_copies if settings is not None else False

        filtered = LibraryStore.filter_entries(
            entries,
            query=query,
            games_only=games_only,
            include_missing=show_missing,
            exclude_adult=hide_adult,
        )
        status = self.popover.status.currentData() if self.popover is not None else "all"
        platform = self.popover.platform.currentData() if self.popover is not None else "all"
        genre = self.popover.genre.currentData() if self.popover is not None else "all"
        if status and status != "all":
            filtered = [e for e in filtered if e.status == status]
        if platform and platform != "all":
            filtered = [e for e in filtered if e.compatibility == platform]
        if genre and genre != "all":
            wanted = str(genre).casefold()
            filtered = [e for e in filtered if any(g.casefold() == wanted for g in e.genres)]
        if self.popover is not None and self.popover.status.currentData() == "all":
            pass

        copies: dict[str, list[LibraryEntry]] = {}
        if group:
            filtered, copies = LibraryStore.group_copies(filtered)
        self._copies = copies
        self._shown = LibraryStore.sort_entries(
            filtered, self._sort_key, self._descending
        )
        self._render()
        self._update_stats()

    def _update_stats(self) -> None:
        total = sum(max(0, e.size) for e in self._entries if e.status != "missing")
        free = platform_support.free_space(self.download_dir)
        hidden = max(0, len(self._entries) - len(self._shown))
        bits = [f"{len(self._shown)} shown", f"{len(self._entries)} games"]
        if hidden:
            bits.append(f"{hidden} hidden")
        if total:
            bits.append(human_size(total))
        if free:
            bits.append(f"{human_size(free)} free")
        self.stats.setText(" · ".join(bits))

    def _sort_label(self) -> str:
        for key, text in LIBRARY_SORTS:
            if key == self._sort_key:
                return text
        return "Title"

    def _sort_menu(self) -> None:
        popover = Popover(self)
        for key, text in LIBRARY_SORTS:
            btn = QPushButton(text)
            btn.setObjectName("popoverRow")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key, p=popover: self._set_sort(k, p))
            popover.add_widget(btn)
        popover.show_at(self.sort)
        self._sort_popover = popover

    def _set_sort(self, key: str, popover: Popover) -> None:
        self._sort_key = key
        self._descending = None
        self.sort.setText(self._sort_label())
        self.direction.set_down(bool(SORT_DEFAULT_DESCENDING.get(key, False)))
        popover.hide()
        self.refresh()

    def _flip(self) -> None:
        current = (
            bool(SORT_DEFAULT_DESCENDING.get(self._sort_key, False))
            if self._descending is None else self._descending
        )
        self._descending = not current
        self.direction.set_down(self._descending)
        self.refresh()

    def _toggle_view(self) -> None:
        self._view = "list" if self._view == "grid" else "grid"
        self.view_toggle.set_list(self._view == "list")
        self.refresh()

    # rendering -------------------------------------------------------------
    def _render(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        clear_layout(self.list_column)
        self._cards.clear()

        self.grid_host.setVisible(self._view == "grid")
        self.list_host.setVisible(self._view == "list")
        if self._view == "grid":
            columns = self._columns()
            available = max(self.scroll.viewport().width() - 48, 200)
            cell = max(180, (available - GRID_GAP * (columns - 1)) // columns)
            for index, entry in enumerate(self._shown):
                card: QWidget = LibraryCard(entry)
                card.setMinimumWidth(min(200, cell))
                card.setMaximumWidth(cell)
                self.grid.addWidget(card, index // columns, index % columns)
                self._wire(card, entry)
        else:
            for entry in self._shown:
                card = LibraryRow(entry)
                self.list_column.addWidget(card)
                self._wire(card, entry)

        has = bool(self._shown)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            if self._entries:
                self.empty.set_text("Nothing matches these filters",
                                    "Clear or adjust the filters in the Filter menu.")
            else:
                self.empty.set_text(
                    "No games yet",
                    "Download something from Browse, or rescan after copying a game folder.",
                )
        card = self._cards.get(self._selected)
        if card is not None:
            card.set_selected(True)  # type: ignore[attr-defined]
        self._load_covers()

    def _wire(self, card: QWidget, entry: LibraryEntry) -> None:
        card.clicked.connect(self.select)  # type: ignore[attr-defined]
        self._cards[entry.identifier] = card
        if entry.identifier in self._pix:
            card.set_pixmap(self._pix[entry.identifier]) if hasattr(card, "set_pixmap") else None

    def _columns(self) -> int:
        width = max(self.scroll.viewport().width() - 48, 200)
        return max(1, int((width + GRID_GAP) // (GRID_MIN_CELL + GRID_GAP)))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._schedule_render()
        return super().eventFilter(watched, event)

    def _schedule_render(self) -> None:
        """Re-flow once, after the grid's own width settles."""
        if getattr(self, "_render_pending", False):
            return
        self._render_pending = True

        def run() -> None:
            self._render_pending = False
            self._render()

        QTimer.singleShot(0, run)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = self.scroll.viewport().width()
        if abs(width - getattr(self, "_last_width", 0)) > 8:
            self._last_width = width
            self._last_columns = self._columns()
            if self._cards:
                self._schedule_render()

    def _load_covers(self) -> None:
        for entry in self._shown:
            card = self._cards.get(entry.identifier)
            if card is None or getattr(card, "_cover_asked", False):
                continue
            card._cover_asked = True  # type: ignore[attr-defined]
            cover = entry.cover_path
            if not cover:
                continue
            path = Path(cover)
            if not path.is_file():
                continue

            def fetch(p: Path = path) -> bytes:
                return p.read_bytes()

            run_async(
                fetch,
                lambda data, i=entry.identifier: self._cover_ready(i, pixmap_from_bytes(data)),
            )

    def _cover_ready(self, identifier: str, pix: QPixmap | None) -> None:
        if pix is None:
            return
        self._pix[identifier] = pix
        card = self._cards.get(identifier)
        if card is not None and hasattr(card, "cover"):
            card.cover.set_pixmap(pix)  # type: ignore[attr-defined]
        if identifier == self._selected:
            entry = self._entry(identifier)
            if entry is not None:
                self.detail.show_entry(entry, pix)

    # selection -------------------------------------------------------------
    def _entry(self, identifier: str) -> LibraryEntry | None:
        return next((e for e in self._entries if e.identifier == identifier), None)

    def select(self, identifier: str) -> None:
        previous = self._cards.get(self._selected)
        if previous is not None:
            previous.set_selected(False)  # type: ignore[attr-defined]
        self._selected = identifier
        card = self._cards.get(identifier)
        if card is not None:
            card.set_selected(True)  # type: ignore[attr-defined]
        entry = self._entry(identifier)
        if entry is not None:
            self.detail.show_entry(entry, self._pix.get(identifier))
            self.detail.show()

    def clear_selection(self) -> None:
        card = self._cards.get(self._selected)
        if card is not None:
            card.set_selected(False)  # type: ignore[attr-defined]
        self._selected = ""
        self.detail.hide()

    # filters ---------------------------------------------------------------
    def _show_filters(self) -> None:
        popover = LibraryFilters(self)
        popover.status.setCurrentIndex(
            max(0, popover.status.findData(
                self.popover.status.currentData() if self.popover is not None else "all"
            ))
        )
        if self.popover is not None:
            popover.platform.setCurrentIndex(
                max(0, popover.platform.findData(self.popover.platform.currentData()))
            )
            popover.genre.setCurrentIndex(
                max(0, popover.genre.findData(self.popover.genre.currentData()))
            )
        popover.changed.connect(self._filters_changed)
        self.popover = popover
        popover.show_at(self.filter_btn)
        self.filter_btn.setChecked(True)

    def _filters_changed(self) -> None:
        popover = self.popover
        if popover is None or self.settings is None:
            self.refresh()
            return
        self.settings.set_library_games_only(not popover.games_only.isChecked())
        self.settings.set_library_show_missing(not popover.show_missing.isChecked())
        self.settings.set_library_group_copies(popover.group_copies.isChecked())
        self.settings.set_library_hide_adult(popover.hide_adult.isChecked())
        self.refresh()

    # actions ---------------------------------------------------------------
    def _more_menu(self) -> None:
        popover = Popover(self)
        actions = (
            ("Show duplicate files…", self.show_duplicates),
            ("Export library…", self.export_library),
            ("Import library…", self.import_library),
            ("Forget missing entries", self.forget_missing),
        )
        for index, (text, callback) in enumerate(actions):
            if index == 3:
                line = QFrame()
                line.setFixedHeight(1)
                line.setObjectName("detailDivider")
                popover.add_widget(line)
            btn = QPushButton(text)
            btn.setObjectName("popoverAction")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=callback, p=popover: (p.hide(), c()))
            popover.add_widget(btn)
        popover.show_at(self.more)
        self._more_popover = popover

    def show_duplicates(self) -> None:
        if self.store is None:
            return
        store = self.store

        def done(result: object) -> None:
            groups = list(result) if isinstance(result, list) else []
            DuplicatesDialog(groups, self).exec()

        run_async(lambda: store.find_duplicates(), done,
                  lambda exc: self._toast(f"Could not scan for duplicates: {exc}", error=True))

    def export_library(self) -> None:
        if self.store is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export library", str(self.download_dir / "library-export.json"),
            "JSON files (*.json)",
        )
        if not path:
            return

        def done(count: object) -> None:
            self._toast(f"Exported {count} entries.")

        run_async(lambda: self.store.export_json(Path(path)), done,
                  lambda exc: self._toast(f"Export failed: {exc}", error=True))

    def import_library(self) -> None:
        if self.store is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import library", str(self.download_dir), "JSON files (*.json)"
        )
        if not path:
            return

        def done(report: object) -> None:
            imported = getattr(report, "imported", 0)
            skipped = getattr(report, "skipped", 0)
            self.set_entries(self.store.entries())
            self._toast(f"Imported {imported} entries · {skipped} skipped.")

        run_async(lambda: self.store.import_json(Path(path)), done,
                  lambda exc: self._toast(f"Import failed: {exc}", error=True))

    def forget_missing(self) -> None:
        if self.store is None:
            return
        removed = self.store.forget_missing()
        self.set_entries(self.store.entries())
        self._toast(f"Forgot {removed} missing entr{'y' if removed == 1 else 'ies'}.")

    def _send_external(self, kind: str, entry: LibraryEntry) -> None:
        """Hand this game's own links to a download manager the user runs."""
        links: list[str] = []
        for value in (entry.magnet, entry.source_url):
            text = (value or "").strip()
            if text and text not in links:
                links.append(text)
        if not links:
            self._toast(
                "This game has no magnet link or source page to send.", error=True
            )
            return
        if kind == "jd":
            sender = external_dm.send_to_jdownloader
            name = "JDownloader 2"
        else:
            sender = external_dm.send_to_ab_download_manager
            name = "AB Download Manager"
        self._toast(f"Sending {len(links)} link(s) to {name}…")

        def done(_result: object) -> None:
            self._toast(f"{name} accepted the link(s).")

        def failed(exc: object) -> None:
            self._toast(f"{name} did not accept them: {exc}", error=True)

        run_async(lambda: sender(links), done, failed)

    def _on_action(self, action: str, identifier: str) -> None:
        entry = self._entry(identifier)
        if entry is None:
            return
        folder = Path(entry.folder) if entry.folder else None
        if action == "open_folder" or action == "show_files":
            if folder is None or not folder.is_dir():
                self._toast("The game folder no longer exists.", error=True)
                return
            platform_support.open_folder(folder)
        elif action == "edit":
            self._edit(entry)
        elif action == "favorite":
            if self.store is not None:
                self.store.toggle_favorite(identifier)
                self._entries = self.store.entries()
                refreshed = self._entry(identifier)
                if refreshed is not None:
                    self.detail.show_entry(refreshed, self._pix.get(identifier))
                self.refresh()
        elif action == "magnet":
            self.magnet_requested.emit(identifier)
        elif action in ("jd", "ab"):
            self._send_external(action, entry)
        elif action == "verify":
            self._verify(entry)
        elif action == "launch":
            self._launch(entry)
        elif action == "remove":
            self._remove(entry)

    def _remove(self, entry: LibraryEntry) -> None:
        """Forget one library entry; the folder and its files are left alone."""
        if self.store is None:
            return
        name = entry.title or entry.identifier
        answer = QMessageBox.question(
            self,
            "Remove from library",
            f"Remove “{name}” from the library?\n\n"
            "Only the library index changes — the folder and its files stay on disk.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.store.remove(entry.identifier)
        self._entries = self.store.entries()
        self._selected = ""
        self.detail.hide()
        self.refresh()
        self.changed.emit()
        self._toast(f"Removed {name} from the library.")

    def _edit(self, entry: LibraryEntry) -> None:
        dialog = EditDetailsDialog(entry, self)
        if dialog.exec() != QDialog.Accepted or self.store is None:
            return
        try:
            self.store.set_user_metadata(entry.identifier, dialog.values())
        except Exception as exc:  # noqa: BLE001 - reported in the UI
            self._toast(f"Could not save details: {exc}", error=True)
            return
        self._entries = self.store.entries()
        refreshed = self._entry(entry.identifier)
        if refreshed is not None:
            self.detail.show_entry(refreshed, self._pix.get(entry.identifier))
        self.refresh()
        self._toast("Details saved.")

    def _verify(self, entry: LibraryEntry) -> None:
        if self.client is None or self.store is None:
            return
        folder = Path(entry.folder) if entry.folder else None
        if folder is None or not folder.is_dir():
            self._toast("The game folder no longer exists.", error=True)
            return
        self._toast("Checking the downloaded files…")
        client = self.client

        def work() -> object:
            item = client.fetch_item(entry.identifier)
            by_name = {file.name: file for file in item.files}
            checked = 0
            match = 0
            mismatch = 0
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or path.name == MANIFEST_NAME:
                    continue
                file = by_name.get(path.name)
                if file is None:
                    continue
                result = integrity.verify_file(path, file)
                checked += 1
                if integrity.is_verified(result):
                    match += 1
                elif getattr(result, "status", "") not in ("unavailable", "UNVERIFIED"):
                    mismatch += 1
            return checked, match, mismatch

        def done(result: object) -> None:
            checked, match, mismatch = result  # type: ignore[misc]
            if checked == 0:
                message = "No published checksum was available for these files."
                entry.integrity = ""
            elif mismatch:
                message = f"{match} of {checked} files matched; {mismatch} did not."
                entry.integrity = f"{mismatch} files failed verification"
            else:
                message = f"All {checked} checked files matched their published digest."
                entry.integrity = f"Verified ({checked} files)"
            try:
                self.store.upsert(entry)
            except Exception:  # noqa: BLE001 - the message still matters
                pass
            self.detail.show_entry(entry, self._pix.get(entry.identifier))
            self._toast(message, error=bool(mismatch))

        run_async(work, done, lambda exc: self._toast(f"Verification failed: {exc}", error=True))

    def _launch(self, entry: LibraryEntry) -> None:
        path = self.detail.chosen_path()
        if path is None:
            self._toast("Choose a file to start the game first.", error=True)
            return
        if platform_support.is_admin():
            self._toast("Refusing to launch games while running as administrator.", error=True)
            return
        if not path.is_file():
            self._toast("That file no longer exists.", error=True)
            return
        dialog = LaunchDialog(path, entry.title or entry.identifier, self)
        if dialog.exec() != QDialog.Accepted:
            return
        # The same two checks the GTK launcher makes, so a path from the detail
        # panel cannot start something outside the library: this frontend has
        # its own Windows command branches, because launch.plan_launch is
        # POSIX-only (os.geteuid, execute bits).
        if not is_within(path, self.download_dir):
            self._toast(
                "The file is outside the selected download folder. It was not launched.",
                error=True,
            )
            return
        suffix = path.suffix.lower()
        if suffix not in (".exe", ".com", ".bat", ".cmd"):
            self._toast(
                "Only .exe, .com, .bat, and .cmd files are started from here. "
                "The file was not started.",
                error=True,
            )
            return
        if suffix in (".bat", ".cmd"):
            command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(path)]
        else:
            command = [str(path)]
        log_file, _log_path = open_launch_log(path.parent)
        try:
            if log_file is not None:
                log_file.write(
                    f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} launching {path.name} ===\n"
                )
                log_file.write("command: " + " ".join(command) + "\n")
                log_file.flush()
            process = subprocess.Popen(
                command,
                cwd=str(path.parent),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=log_file if log_file is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                **platform_support.detached_kwargs(),
            )
        except OSError as exc:
            self._toast(f"Could not start the program: {exc}", error=True)
            return
        finally:
            if log_file is not None:
                log_file.close()
        self._toast(f"Started {path.name} (pid {process.pid}).")

    # helpers ---------------------------------------------------------------
    def _toast(self, message: str, *, error: bool = False) -> None:
        window = self.window()
        toast = getattr(window, "toast", None)
        if isinstance(toast, Toast):
            toast.show_message(message, error=error)
        elif error:
            QMessageBox.warning(self, "Lumora", message)


__all__ = [
    "EditDetailsDialog",
    "LaunchDialog",
    "LibraryCard",
    "LibraryDetail",
    "LibraryFilters",
    "LibraryPage",
    "LibraryRow",
    "launch_candidates",
]
