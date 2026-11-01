"""Downloads drawer for the Qt frontend.

The download manager runs on its own worker thread and calls an observer from
there; ``DownloadBridge`` turns that call into a Qt signal so the UI is only
ever touched from the UI thread.  Row text and button rules are plain functions
(``detail_text``, ``actions_for``) so they can be tested without a display.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import platform_support
from .archive import ArchiveFile, ArchiveItem
from .downloader import DownloadManager, DownloadStatus, TaskSnapshot
from .library import game_folder, write_manifest
from .qt_widgets import clear_layout, run_async
from .util import AppError, check_download_dir, human_eta, human_size, human_speed

#: Row refreshes closer together than this are merged into one.
REFRESH_MS = 150


# --------------------------------------------------------------------------
# pure logic (no widgets)
# --------------------------------------------------------------------------


def detail_text(snap: TaskSnapshot) -> str:
    """The one-line status shown under a download's progress bar."""
    status = snap.status
    if status in (DownloadStatus.RUNNING, DownloadStatus.PAUSED):
        if snap.total:
            text = f"{human_size(snap.downloaded)} of {human_size(snap.total)}"
        else:
            text = f"{human_size(snap.downloaded)} downloaded · unknown size"
        if status == DownloadStatus.PAUSED:
            return f"Paused · {text}"
        if snap.speed > 0:
            text += f" · {human_speed(snap.speed)}"
            if snap.eta is not None:
                text += f" · {human_eta(snap.eta)} left"
        return text
    if status == DownloadStatus.QUEUED:
        return "Waiting to start"
    if status == DownloadStatus.COMPLETED:
        return f"Saved to {snap.folder}"
    if status == DownloadStatus.CANCELLED:
        return "Cancelled · partial file kept"
    reason = snap.error or "unknown error"
    if snap.retry_limit > 0:
        return f"Failed: {reason} · retry {snap.attempts} of {snap.retry_limit} used"
    return f"Failed: {reason}"


def actions_for(snap: TaskSnapshot) -> list[str]:
    """Which buttons a row offers, in display order."""
    status = snap.status
    if status == DownloadStatus.RUNNING:
        return ["pause", "cancel"]
    if status == DownloadStatus.PAUSED:
        return ["resume", "cancel"]
    if status == DownloadStatus.QUEUED:
        return ["cancel"]
    if status == DownloadStatus.COMPLETED:
        return ["open", "remove"]
    actions = ["retry"] if snap.can_retry else []
    return actions + ["remove"]


def active_count(snaps: Sequence[TaskSnapshot]) -> int:
    return sum(1 for s in snaps if not s.status.terminal)


def downloadable_files(item: ArchiveItem) -> list[ArchiveFile]:
    """Files of an archive item the user may fetch (private ones are skipped)."""
    return [f for f in item.files if f.downloadable]


def default_selection(files: Sequence[ArchiveFile]) -> list[ArchiveFile]:
    """Game payloads, not the metadata archive.org attaches to every item."""
    return [f for f in files if not f.auxiliary]


_ACTION_LABELS = {
    "pause": "Pause",
    "resume": "Resume",
    "cancel": "Cancel",
    "retry": "Retry",
    "remove": "Remove",
    "open": "Show folder",
}


# --------------------------------------------------------------------------
# thread bridge
# --------------------------------------------------------------------------


class DownloadBridge(QObject):
    """Lets the manager's worker thread poke the UI thread safely."""

    changed = Signal()

    def notify(self) -> None:  # may be called from any thread
        self.changed.emit()


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------


class DownloadRow(QFrame):
    """One download: name, game title, progress bar, status line, buttons."""

    action = Signal(str, str)  # task id, action name

    def __init__(self, snap: TaskSnapshot, game_title: str) -> None:
        super().__init__()
        self.setObjectName("dl-row")
        self.task_id = snap.id
        self._actions: list[str] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.name = QLabel()
        self.name.setObjectName("dl-name")
        self.game = QLabel()
        self.game.setObjectName("dl-game")
        names.addWidget(self.name)
        names.addWidget(self.game)
        top.addLayout(names, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        top.addLayout(self.buttons)
        col.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setObjectName("dl-bar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        col.addWidget(self.bar)

        self.detail = QLabel()
        self.detail.setObjectName("dl-detail")
        col.addWidget(self.detail)

        self.update_from(snap, game_title)

    def update_from(self, snap: TaskSnapshot, game_title: str) -> None:
        self.name.setText(Path(snap.filename).name)
        self.name.setToolTip(snap.filename)
        self.game.setText(game_title)
        self.game.setVisible(bool(game_title) and game_title != Path(snap.filename).name)
        self.detail.setText(detail_text(snap))
        self.setProperty("state", snap.status.value)
        self.style().unpolish(self)
        self.style().polish(self)

        progress = snap.progress
        if snap.status == DownloadStatus.COMPLETED:
            self.bar.setRange(0, 1000)
            self.bar.setValue(1000)
        elif progress is None:
            if snap.status == DownloadStatus.RUNNING:
                self.bar.setRange(0, 0)  # busy indicator: total size unknown
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(progress * 1000))

        actions = actions_for(snap)
        if actions != self._actions:  # rebuild only when the button set changes
            self._actions = actions
            clear_layout(self.buttons)
            for name in actions:
                btn = QPushButton(_ACTION_LABELS[name])
                btn.setObjectName("flat" if name in ("remove", "open") else "small")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _=False, n=name: self.action.emit(self.task_id, n))
                self.buttons.addWidget(btn)


class DownloadsPanel(QFrame):
    """The drawer under the main view: every queued, running and finished download."""

    closed = Signal()
    count_changed = Signal(int)

    def __init__(
        self,
        manager: DownloadManager,
        download_dir: Callable[[], Path],
        title_for: Callable[[str], str],
    ) -> None:
        super().__init__()
        self.setObjectName("downloads")
        self.setFixedHeight(300)
        self._manager = manager
        self._download_dir = download_dir
        self._title_for = title_for
        self._rows: dict[str, DownloadRow] = {}
        self._last_active = -1

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        head = QFrame()
        head.setObjectName("dl-head")
        h = QHBoxLayout(head)
        h.setContentsMargins(16, 10, 12, 10)
        heading = QVBoxLayout()
        heading.setSpacing(0)
        title = QLabel("Downloads")
        title.setObjectName("page-title")
        sub = QLabel("One download runs at a time. Queued and paused items are kept between sessions.")
        sub.setObjectName("detail-note")
        heading.addWidget(title)
        heading.addWidget(sub)
        h.addLayout(heading, 1)
        folder = QPushButton("Open folder")
        folder.clicked.connect(self._open_folder)
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self._clear_finished)
        close = QPushButton("✕")
        close.setObjectName("flat")
        close.setToolTip("Hide downloads")
        close.clicked.connect(self.closed.emit)
        for w in (folder, clear, close):
            h.addWidget(w)
        col.addWidget(head)

        self.empty = QLabel("No downloads yet. Pick a game in Archive and press Download.")
        self.empty.setObjectName("empty-sub")
        self.empty.setAlignment(Qt.AlignCenter)
        col.addWidget(self.empty, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        self.rows_layout = QVBoxLayout(host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(host)
        col.addWidget(self.scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    # refreshing ----------------------------------------------------------
    def schedule_refresh(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def refresh(self) -> None:
        snaps = self._manager.snapshots()
        seen: set[str] = set()
        for index, snap in enumerate(snaps):
            seen.add(snap.id)
            title = self._title_for(snap.identifier)
            row = self._rows.get(snap.id)
            if row is None:
                row = DownloadRow(snap, title)
                row.action.connect(self._on_action)
                self._rows[snap.id] = row
                self.rows_layout.insertWidget(index, row)
            else:
                row.update_from(snap, title)
        for task_id in [t for t in self._rows if t not in seen]:
            row = self._rows.pop(task_id)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self.empty.setVisible(not snaps)
        self.scroll.setVisible(bool(snaps))
        active = active_count(snaps)
        if active != self._last_active:
            self._last_active = active
            self.count_changed.emit(active)

    # actions -------------------------------------------------------------
    def _on_action(self, task_id: str, action: str) -> None:
        manager = self._manager
        if action == "pause":
            manager.pause(task_id)
        elif action == "resume":
            manager.resume(task_id)
        elif action == "cancel":
            manager.cancel(task_id)
        elif action == "retry":
            manager.retry(task_id)
        elif action == "remove":
            manager.remove(task_id)
        elif action == "open":
            snap = manager.get(task_id)
            if snap is not None:
                platform_support.open_folder(snap.folder)
        self.refresh()

    def _open_folder(self) -> None:
        platform_support.open_folder(self._download_dir())

    def _clear_finished(self) -> None:
        self._manager.clear_finished()
        self.refresh()


class FilePickDialog(QDialog):
    """Choose which files of an archive item to download."""

    def __init__(self, title: str, files: Sequence[ArchiveFile], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose files to download")
        self.setMinimumSize(520, 420)
        self._files = list(files)
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 16)
        col.setSpacing(10)
        head = QLabel(title)
        head.setObjectName("detail-title")
        head.setWordWrap(True)
        note = QLabel("Game files are ticked. Tick the extras only if you want them.")
        note.setObjectName("detail-note")
        col.addWidget(head)
        col.addWidget(note)

        self.list = QListWidget()
        preselected = {f.name for f in default_selection(self._files)}
        for f in self._files:
            item = QListWidgetItem(f"{f.name}    ·    {human_size(f.size)}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if f.name in preselected else Qt.Unchecked)
            self.list.addItem(item)
        col.addWidget(self.list, 1)

        buttons = QDialogButtonBox()
        self.ok = buttons.addButton("Download", QDialogButtonBox.AcceptRole)
        self.ok.setObjectName("suggested")
        buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

    def chosen(self) -> list[ArchiveFile]:
        return [
            f for f, index in zip(self._files, range(self.list.count()))
            if self.list.item(index).checkState() == Qt.Checked
        ]


# --------------------------------------------------------------------------
# queueing
# --------------------------------------------------------------------------


def enqueue_files(
    manager: DownloadManager,
    item: ArchiveItem,
    files: Sequence[ArchiveFile],
    download_dir: Path,
    on_done: Callable[[int, list[str], str | None], None],
    on_error: Callable[[object], None] | None = None,
) -> None:
    """Add ``files`` to the queue off the UI thread, then report the outcome.

    ``on_done(added, errors, manifest_note)`` runs on the UI thread.
    """
    chosen = list(files)

    def work() -> tuple[int, list[str], str | None]:
        root = check_download_dir(download_dir)
        added = 0
        errors: list[str] = []
        for archive_file in chosen:
            try:
                manager.add(
                    identifier=item.identifier,
                    filename=archive_file.name,
                    url=item.file_url(archive_file),
                    download_dir=root,
                    total_size=archive_file.size,
                )
                added += 1
            except (AppError, ValueError, OSError) as exc:
                errors.append(f"{archive_file.name}: {str(exc).strip() or exc.__class__.__name__}")
        note: str | None = None
        if added:
            try:
                write_manifest(game_folder(root, item.identifier), item)
            except (OSError, ValueError) as exc:
                note = f"Game metadata was not saved: {exc}"
        return added, errors, note

    def done(result: object) -> None:
        added, errors, note = result  # type: ignore[misc]
        on_done(added, errors, note)

    run_async(work, done, on_error)
