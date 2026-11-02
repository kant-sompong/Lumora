"""Downloads page for the Qt frontend.

The download manager runs on its own worker thread and calls an observer from
there; ``DownloadBridge`` turns that call into a Qt signal so the UI is only
ever touched from the UI thread.  Row text and button rules are plain functions
(``detail_text``, ``actions_for``) so they can be tested without a display.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
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
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import external_dm, platform_support
from .archive import ArchiveFile, ArchiveItem
from .downloader import DownloadManager, DownloadStatus, TaskSnapshot
from .library import game_folder, write_manifest
from .qt_widgets import EmptyState, Toast, clear_layout, label, run_async
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


def should_notify(previous: object, snap: TaskSnapshot) -> bool:
    """True when a task just moved from an unfinished state to finished/failed."""
    if snap.status not in (DownloadStatus.COMPLETED, DownloadStatus.FAILED):
        return False
    if not isinstance(previous, DownloadStatus):
        return False
    return previous != snap.status and not previous.terminal


def unfinished_count(snaps: Sequence[TaskSnapshot]) -> int:
    """Downloads left over from an earlier session, waiting to be restarted."""
    return sum(
        1
        for snap in snaps
        if snap.status
        in (DownloadStatus.PAUSED, DownloadStatus.QUEUED, DownloadStatus.FAILED, DownloadStatus.CANCELLED)
    )


def resume_leftovers(manager: DownloadManager) -> int:
    """Restart every leftover task that can be restarted; return how many."""
    resumed = 0
    for snap in manager.snapshots():
        try:
            if snap.status == DownloadStatus.PAUSED:
                manager.resume(snap.id)
                resumed += 1
            elif snap.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED) and snap.can_retry:
                manager.retry(snap.id)
                resumed += 1
        except (AppError, ValueError, OSError):
            continue
    return resumed


def send_to_external(name: str, urls: Sequence[object], folder: str = "") -> int:
    """Hand links to JDownloader or AB Download Manager; return how many were sent."""
    if name == "jdownloader":
        return external_dm.send_to_jdownloader(urls, source="Lumora")
    if name == "ab_download_manager":
        return external_dm.send_to_ab_download_manager(urls, folder=folder)
    raise ValueError(f"Unknown download manager: {name}")


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
    clicked = Signal(str)

    def __init__(self, snap: TaskSnapshot, game_title: str) -> None:
        super().__init__()
        self.setObjectName("dlRow")
        self.setCursor(Qt.PointingHandCursor)
        self.task_id = snap.id
        self._actions: list[str] = []
        self._selected = False

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 12, 20, 12)
        col.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(8)
        names = QVBoxLayout()
        names.setSpacing(1)
        self.name = QLabel()
        self.name.setObjectName("dlName")
        self.game = QLabel()
        self.game.setObjectName("dlGame")
        names.addWidget(self.name)
        names.addWidget(self.game)
        top.addLayout(names, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(6)
        top.addLayout(self.buttons)
        col.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setObjectName("dlBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.bar.setRange(0, 1000)
        col.addWidget(self.bar)

        self.detail = QLabel()
        self.detail.setObjectName("dlDetail")
        col.addWidget(self.detail)

        self.update_from(snap, game_title)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self.setStyleSheet("QFrame#dlRow { background: rgba(59, 130, 246, 0.08); }" if on else "")

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
                btn.setObjectName("small" if name != "open" else "quiet")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _=False, n=name: self.action.emit(self.task_id, n))
                self.buttons.addWidget(btn)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.task_id)
        super().mouseReleaseEvent(event)


class TorrentRow(QFrame):
    """One magnet download in the bottom strip."""

    def __init__(self, title: str, status: str, fraction: float, detail: str) -> None:
        super().__init__()
        self.setObjectName("dlRow")
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 8, 20, 8)
        column.setSpacing(3)
        top = QHBoxLayout()
        name = label(title, "dlName")
        top.addWidget(name, 1)
        top.addWidget(label(status, "dlDetail"))
        column.addLayout(top)
        bar = QProgressBar()
        bar.setObjectName("dlBar")
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setRange(0, 1000)
        bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        column.addWidget(bar)
        column.addWidget(label(detail, "dlDetail"))


class DownloadsPage(QWidget):
    """Downloads as a destination page, with the torrent strip at the bottom."""

    closed = Signal()
    count_changed = Signal(int)

    def __init__(
        self,
        manager: DownloadManager,
        download_dir: Callable[[], Path],
        title_for: Callable[[str], str],
        torrents: object | None = None,
        cover_for: Callable[[str], str] | None = None,
        settings: object | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._download_dir = download_dir
        self._title_for = title_for
        self._torrents = torrents
        self._settings = settings
        self._rows: dict[str, DownloadRow] = {}
        self._last_active = -1
        self._selected = ""
        self._status_before: dict[str, DownloadStatus] = {}
        self._banner_dismissed = False
        self._tray: QSystemTrayIcon | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        head = QHBoxLayout(header)
        head.setContentsMargins(24, 16, 24, 12)
        head.setSpacing(6)
        head.addWidget(label("DOWNLOADS", "eyebrow"))
        head.addStretch(1)
        for text, tip, callback in (
            ("Pause", "Pause the selected download (or every running one)", lambda: self._bulk("pause")),
            ("Cancel", "Cancel the selected download (or every active one)", lambda: self._bulk("cancel")),
            ("Retry", "Retry the selected download (or every failed one)", lambda: self._bulk("retry")),
            ("Remove", "Remove the selected download from the queue", lambda: self._bulk("remove")),
            ("Clear finished", "Remove every finished download from the queue", self._clear_finished),
        ):
            btn = QPushButton(text)
            btn.setObjectName("small")
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=callback: c())
            head.addWidget(btn)
        self.refresh_btn = QPushButton("⌄")
        self.refresh_btn.setObjectName("small")
        self.refresh_btn.setToolTip("Refresh the queue")
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        root.addWidget(header)

        line = QFrame()
        line.setObjectName("metabar")
        line.setFixedHeight(1)
        root.addWidget(line)

        self.banner = QFrame()
        self.banner.setObjectName("dlBanner")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(24, 8, 24, 8)
        banner_row.setSpacing(8)
        self.banner_text = label("", "statLine")
        banner_row.addWidget(self.banner_text)
        banner_row.addStretch(1)
        self.banner_resume = QPushButton("Resume all")
        self.banner_resume.setObjectName("small")
        self.banner_resume.setCursor(Qt.PointingHandCursor)
        self.banner_resume.setToolTip("Restart every download left from an earlier session")
        self.banner_resume.clicked.connect(self._resume_unfinished)
        banner_row.addWidget(self.banner_resume)
        self.banner_dismiss = QPushButton("Not now")
        self.banner_dismiss.setObjectName("quiet")
        self.banner_dismiss.setCursor(Qt.PointingHandCursor)
        self.banner_dismiss.clicked.connect(self._dismiss_banner)
        banner_row.addWidget(self.banner_dismiss)
        self.banner.hide()
        root.addWidget(self.banner)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVisible(False)
        host = QWidget()
        self.rows_layout = QVBoxLayout(host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch(1)
        self.scroll.setWidget(host)
        root.addWidget(self.scroll, 1)

        self.empty = EmptyState(
            "downloads", "No downloads yet",
            "Select a game and add files to see them here.",
        )
        root.addWidget(self.empty, 1)

        # Torrents -----------------------------------------------------------------
        self.torrent_host = QFrame()
        self.torrent_host.setObjectName("dlFoot")
        torrent_column = QVBoxLayout(self.torrent_host)
        torrent_column.setContentsMargins(0, 0, 0, 0)
        torrent_column.setSpacing(0)
        strip = QWidget()
        strip_row = QHBoxLayout(strip)
        strip_row.setContentsMargins(24, 8, 24, 8)
        strip_row.setSpacing(8)
        strip_row.addWidget(label("TORRENTS", "eyebrow"))
        self.torrent_engine = label("", "statLine")
        strip_row.addWidget(self.torrent_engine)
        strip_row.addStretch(1)
        for text in ("Clear finished", "Remove", "Pause", "Start"):
            btn = QPushButton(text)
            btn.setObjectName("small")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, action=text.lower(): self._torrent_action(action))
            strip_row.addWidget(btn)
        torrent_column.addWidget(strip)
        self.torrent_empty = label(
            "No torrent downloads.\nMagnet downloads appear here while a torrent engine runs.",
            "emptySub",
        )
        self.torrent_empty.setAlignment(Qt.AlignCenter)
        self.torrent_empty.setContentsMargins(0, 10, 0, 12)
        torrent_column.addWidget(self.torrent_empty)
        self.torrent_rows_host = QWidget()
        self.torrent_rows = QVBoxLayout(self.torrent_rows_host)
        self.torrent_rows.setContentsMargins(0, 0, 0, 0)
        self.torrent_rows.setSpacing(0)
        self.torrent_rows_host.hide()
        torrent_column.addWidget(self.torrent_rows_host)
        root.addWidget(self.torrent_host)
        self.torrent_host.setVisible(torrents is not None)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._torrent_timer = QTimer(self)
        self._torrent_timer.setInterval(2000)
        self._torrent_timer.timeout.connect(self._poll_torrents)
        if torrents is not None:
            self._torrent_timer.start()
        self.refresh()

    def _poll_torrents(self) -> None:
        if self.isVisible():
            self._refresh_torrents()

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
                row.clicked.connect(self._select)
                self._rows[snap.id] = row
                self.rows_layout.insertWidget(index, row)
            else:
                row.update_from(snap, title)
            row.set_selected(snap.id == self._selected)
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
        self._notice_finished(snaps)
        self._update_banner(snaps)
        self._refresh_torrents()

    # notices ---------------------------------------------------------------
    def _notice_finished(self, snaps: Sequence[TaskSnapshot]) -> None:
        """One desktop notification per task that just finished or failed."""
        seen = {snap.id for snap in snaps}
        for snap in snaps:
            previous = self._status_before.get(snap.id)
            if should_notify(previous, snap):
                self._notify_finished(snap)
            self._status_before[snap.id] = snap.status
        for task_id in [t for t in self._status_before if t not in seen]:
            self._status_before.pop(task_id, None)

    def _notifications_on(self) -> bool:
        settings = self._settings
        if settings is None:
            return True
        try:
            return bool(settings.get("desktop_notifications", True))
        except Exception:  # noqa: BLE001 - a settings issue must not stop downloads
            return True

    def _notify_finished(self, snap: TaskSnapshot) -> None:
        if not self._notifications_on():
            return
        name = Path(snap.filename).name
        failed = snap.status == DownloadStatus.FAILED
        title = "Download failed" if failed else "Download finished"
        message = f"{name}: {snap.error or 'unknown error'}" if failed else name
        if platform_support.notify(title, message, error=failed):
            return
        self._tray_message(title, message)

    def _tray_message(self, title: str, message: str) -> None:
        """Windows has no notify-send; a tray balloon is the portable fallback."""
        try:
            if self._tray is None:
                if not QSystemTrayIcon.isSystemTrayAvailable():
                    return
                self._tray = QSystemTrayIcon(self)
                window = self.window()
                pixmap = window.windowIcon().pixmap(32, 32) if window is not None else None
                if pixmap is not None and not pixmap.isNull():
                    self._tray.setIcon(QIcon(pixmap))
            self._tray.show()
            self._tray.showMessage(
                title, message, QSystemTrayIcon.Information, 6000
            )
        except Exception:  # noqa: BLE001 - a notification is never worth a crash
            return

    def _update_banner(self, snaps: Sequence[TaskSnapshot]) -> None:
        count = 0 if self._banner_dismissed else unfinished_count(snaps)
        self.banner.setVisible(bool(count))
        if count:
            self.banner_text.setText(
                f"{count} unfinished download{'s' if count != 1 else ''} from the last "
                "session — resume them or remove them."
            )

    def _resume_unfinished(self) -> None:
        resumed = resume_leftovers(self._manager)
        self._banner_dismissed = True
        self.banner.hide()
        toast = getattr(self.window(), "toast", None)
        if isinstance(toast, Toast):
            toast.show_message(
                f"Resumed {resumed} download{'s' if resumed != 1 else ''}."
                if resumed else "Nothing could be resumed.",
                error=not resumed,
            )
        self.refresh()

    def _dismiss_banner(self) -> None:
        self._banner_dismissed = True
        self.banner.hide()

    def _refresh_torrents(self) -> None:
        if self._torrents is None:
            return
        try:
            info = self._torrents.engine_status()
            self.torrent_engine.setText(f"{getattr(info, 'label', 'Built-in torrent engine')}")
        except Exception:  # noqa: BLE001 - the strip is informational
            self.torrent_engine.setText("Built-in torrent engine")
        clear_layout(self.torrent_rows)
        try:
            tasks = self._torrents.tasks()
        except Exception:  # noqa: BLE001
            tasks = []
        self.torrent_rows_host.setVisible(bool(tasks))
        self.torrent_empty.setVisible(not tasks)
        for task in tasks:
            detail = f"{human_size(task.completed)} of {human_size(task.total)}" if task.total else ""
            if task.speed:
                detail += f" · {human_speed(task.speed)}"
            self.torrent_rows.addWidget(
                TorrentRow(task.title or task.infohash[:12], task.status, task.fraction, detail)
            )

    def _select(self, task_id: str) -> None:
        self._selected = "" if self._selected == task_id else task_id
        for identifier, row in self._rows.items():
            row.set_selected(identifier == self._selected)

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

    def _bulk(self, action: str) -> None:
        snaps = {snap.id: snap for snap in self._manager.snapshots()}
        targets = [self._selected] if self._selected in snaps else list(snaps)
        eligible = {
            "pause": {DownloadStatus.RUNNING},
            "cancel": {DownloadStatus.RUNNING, DownloadStatus.PAUSED, DownloadStatus.QUEUED},
            "retry": {DownloadStatus.FAILED, DownloadStatus.CANCELLED},
            "remove": set(DownloadStatus),
        }[action]
        for task_id in targets:
            snap = snaps[task_id]
            if snap.status not in eligible:
                continue
            if action == "pause":
                self._manager.pause(task_id)
            elif action == "cancel":
                self._manager.cancel(task_id)
            elif action == "retry":
                self._manager.retry(task_id)
            elif action == "remove":
                self._manager.remove(task_id)
                self._selected = ""
        self.refresh()

    def _clear_finished(self) -> None:
        self._manager.clear_finished()
        self.refresh()

    def _torrent_action(self, action: str) -> None:
        if self._torrents is None:
            return
        try:
            if action == "clear finished":
                self._torrents.clear_finished()
            elif action == "pause":
                for task in self._torrents.tasks():
                    if not task.finished:
                        self._torrents.pause(task.task_id)
            elif action == "start":
                for task in self._torrents.tasks():
                    if not task.finished:
                        self._torrents.resume(task.task_id)
            elif action == "remove":
                for task in self._torrents.tasks():
                    if task.finished:
                        self._torrents.remove(task.task_id)
        except Exception as exc:  # noqa: BLE001 - reported, never crashes the page
            window = self.window()
            toast = getattr(window, "toast", None)
            if isinstance(toast, Toast):
                toast.show_message(f"Torrent action failed: {exc}", error=True)
        self._refresh_torrents()

    def _open_folder(self) -> None:
        platform_support.open_folder(self._download_dir())


class FilePickDialog(QDialog):
    """Choose which files of an archive item to download."""

    def __init__(self, title: str, files: Sequence[ArchiveFile], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Choose files to download")
        self.setMinimumSize(520, 420)
        self._files = list(files)
        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 16)
        col.setSpacing(10)
        head = label(title, "pageTitle", wrap=True)
        col.addWidget(head)
        col.addWidget(label("Game files are ticked. Tick the extras only if you want them.",
                            "detailNote"))

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


__all__ = [
    "DownloadBridge",
    "DownloadRow",
    "DownloadsPage",
    "FilePickDialog",
    "TorrentRow",
    "actions_for",
    "active_count",
    "default_selection",
    "detail_text",
    "downloadable_files",
    "enqueue_files",
    "resume_leftovers",
    "send_to_external",
    "should_notify",
    "unfinished_count",
]
