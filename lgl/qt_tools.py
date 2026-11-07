"""The Qt tools window: every 0.6.6 feature on one screen.

Rows come from :mod:`lgl.tool_panels`, so this file only knows how to draw a
toggle, a choice, a button and a line of text.  Long work (backup, support
report, update check) runs through :func:`run_async` so the window never freezes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, backup, diagnostics, platform_support, tool_panels
from .qt_widgets import run_async
from .settings import Settings


def _snapshot(settings: Any) -> dict[str, Any]:
    if settings is None:
        return {}
    for name in ("as_dict", "to_dict"):
        method = getattr(settings, name, None)
        if callable(method):
            try:
                value = method()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, dict):
                return dict(value)
    data = getattr(settings, "data", None)
    return dict(data) if isinstance(data, dict) else {}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class ToolsDialog(QDialog):
    """Browse the feature sections; every change is applied immediately."""

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        profile_store: Any = None,
        media_roots: Sequence[str] = (),
        plugins: Sequence[Any] = (),
        rejected_plugins: Sequence[Any] = (),
        upcoming: Sequence[Any] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tools")
        self.resize(820, 600)
        self.settings = settings
        self.profile_store = profile_store
        self.media_roots = list(media_roots)
        self._plugins = list(plugins)
        self._rejected = list(rejected_plugins)
        self._upcoming = list(upcoming)
        self._panel = tool_panels.Panel()

        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        self._nav = QListWidget()
        self._nav.setFixedWidth(200)
        self._nav.currentRowChanged.connect(self._show_section)
        self._stack = QStackedWidget()
        body.addWidget(self._nav)
        body.addWidget(self._stack, 1)
        layout.addLayout(body, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.reload()

    # -- rendering ------------------------------------------------------
    def reload(self, keep: int | None = None) -> None:
        index = self._nav.currentRow() if keep is None else keep
        self._panel = tool_panels.build(
            self.settings,
            plugins=self._plugins,
            rejected_plugins=self._rejected,
            upcoming=self._upcoming,
            media_roots=self.media_roots,
            profile_store=self.profile_store,
            backup_dir=platform_support.data_dir(),
            diagnostics_path=platform_support.data_dir() / "support-report.txt",
        )
        self._nav.blockSignals(True)
        self._nav.clear()
        while self._stack.count():
            page = self._stack.widget(0)
            self._stack.removeWidget(page)
            page.deleteLater()
        for section in self._panel.sections:
            self._nav.addItem(section.title)
            self._stack.addWidget(self._page(section))
        self._nav.blockSignals(False)
        if self._panel.sections:
            self._nav.setCurrentRow(max(0, min(len(self._panel.sections) - 1, index)))
            self._show_section(self._nav.currentRow())

    def _show_section(self, index: int) -> None:
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)

    def _page(self, section: tool_panels.Section) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(18, 14, 18, 14)
        column.setSpacing(10)
        title = QLabel(section.title)
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        column.addWidget(title)
        if section.description:
            note = QLabel(section.description)
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid);")
            column.addWidget(note)
        for row in section.rows:
            column.addWidget(self._row(row))
        column.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _row(self, row: tool_panels.Row) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 2, 0, 2)
        text_column = QVBoxLayout()
        label = QLabel(row.label)
        label.setWordWrap(True)
        text_column.addWidget(label)
        description = row.description or (row.value if row.kind == tool_panels.INFO else "")
        if description:
            note = QLabel(description)
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid); font-size: 11px;")
            text_column.addWidget(note)
        layout.addLayout(text_column, 1)

        if row.kind == tool_panels.TOGGLE and row.action:
            box = QCheckBox()
            box.setChecked(bool(row.state))
            box.stateChanged.connect(
                lambda state, action=row.action: self._apply(action, bool(state))
            )
            layout.addWidget(box, 0, Qt.AlignTop)
        elif row.kind == tool_panels.CHOICE and row.action:
            combo = QComboBox()
            for value, text in row.options:
                combo.addItem(text, value)
            position = combo.findData(row.value)
            if position >= 0:
                combo.setCurrentIndex(position)
            combo.currentIndexChanged.connect(
                lambda _index, combo=combo, action=row.action: self._apply(
                    action, combo.currentData()
                )
            )
            layout.addWidget(combo, 0, Qt.AlignTop)
        elif row.kind == tool_panels.BUTTON and row.action:
            button = QPushButton(row.value or row.label)
            button.clicked.connect(lambda _checked=False, action=row.action: self._apply(action, True))
            layout.addWidget(button, 0, Qt.AlignTop)
        elif row.value:
            value = QLabel(row.value)
            value.setStyleSheet("color: palette(mid);")
            layout.addWidget(value, 0, Qt.AlignTop)
        return holder

    # -- actions --------------------------------------------------------
    def _apply(self, action: str, value: Any) -> None:
        if action == "updates.check":
            self._say("Checking the releases page…")
            run_async(
                lambda: __import__("lgl.updates", fromlist=["updates"]).check_for_update(__version__),
                self._checked,
                self._failed,
            )
            return
        if action == "backup.create":
            self._say("Writing the backup…")
            run_async(self._backup, self._backup_done, self._failed)
            return
        if action == "diagnostics.create":
            self._say("Writing the support report…")
            run_async(self._report, self._report_done, self._failed)
            return
        if action == "home.reset":
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self,
                "Reset layout",
                "Restore the shelf order, visibility and sizes to the defaults?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if action == "home.shelves":
            from .qt_shelves import ShelvesDialog

            ShelvesDialog(self.settings, self).exec()
            self._say("Shelves saved.")
            self.reload()
            return
        message = tool_panels.apply(
            action,
            self.settings,
            value,
            profile_store=self.profile_store,
            media_roots=self.media_roots,
        )
        self._say(message)
        self.reload()
        if action.startswith("home."):
            self._refresh_home()

    def _refresh_home(self) -> None:
        """Apply a Home layout change behind this window, without a restart."""
        from .qt_browse import BrowsePage

        for page in self.window().findChildren(BrowsePage):
            page.refresh_home_layout()

    def _say(self, message: str) -> None:
        self._status.setText(str(message))

    def _failed(self, error: object) -> None:
        self._say(f"That did not work: {error}")

    def _checked(self, status: Any) -> None:
        self._say(getattr(status, "message", str(status)))

    def _backup(self) -> Any:
        destination = platform_support.data_dir() / "lumora-backup.zip"
        library = _read_json(platform_support.data_dir() / "library.json")
        collections = _read_json(platform_support.data_dir() / "collections.json")
        return backup.create_backup(
            destination,
            library=library,
            collections=collections,
            settings=_snapshot(self.settings),
            app_version=__version__,
        )

    def _backup_done(self, report: Any) -> None:
        path = getattr(report, "path", "")
        parts = getattr(report, "parts", ())
        self._say(f"Backup written to {path} ({len(parts)} part(s)).")

    def _report(self) -> Path:
        return diagnostics.write_report(
            platform_support.data_dir() / "support-report.txt",
            app_version=__version__,
            settings=self.settings,
            extra={"plugins": len(self._plugins)},
        )

    def _report_done(self, path: Any) -> None:
        self._say(f"Support report saved to {path}.")


def show_tools(settings: Settings, parent: QWidget | None = None, **kwargs: Any) -> ToolsDialog:
    dialog = ToolsDialog(settings, parent, **kwargs)
    dialog.exec()
    return dialog


__all__ = ["ToolsDialog", "show_tools"]
