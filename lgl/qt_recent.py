"""The Qt "Recently viewed" window: one list across games, movies and anime."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import recently_viewed

#: How each stored kind is shown next to the title.
KIND_NAMES = {"game": "Game", "movie": "Movie", "tv": "TV", "anime": "Anime"}


class RecentlyViewedDialog(QDialog):
    """Rows of opened titles; opening one tells the window to go there."""

    open_requested = Signal(str, str)  # kind, key

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recently viewed")
        self.resize(560, 480)
        self._path = Path(path)

        layout = QVBoxLayout(self)
        headline = QLabel("RECENTLY VIEWED", self)
        headline.setObjectName("detailEyebrow")
        layout.addWidget(headline)

        self.list = QListWidget(self)
        self.list.itemActivated.connect(self._open_item)
        self.list.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.list, 1)

        self.empty = QLabel(
            "Nothing yet. Open a game, movie, series or anime and it appears here.",
            self,
        )
        self.empty.setObjectName("detailNote")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)

        buttons = QHBoxLayout()
        self.clear_btn = QPushButton("Clear history", self)
        self.clear_btn.clicked.connect(self._clear)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        self.open_btn = QPushButton("Open", self)
        self.open_btn.clicked.connect(lambda: self._open_item(self.list.currentItem()))
        buttons.addWidget(self.open_btn)
        self.close_btn = QPushButton("Close", self)
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        """Rebuild the rows from disk; also used after clearing."""
        self.list.clear()
        entries = recently_viewed.recent(self._path, recently_viewed.MAX_ENTRIES)
        for entry in entries:
            kind = KIND_NAMES.get(entry["kind"], entry["kind"].title())
            text = f"{entry['title']}  ·  {kind}"
            if entry["subtitle"]:
                text += f"\n{entry['subtitle']}"
            item = QListWidgetItem(text, self.list)
            item.setData(Qt.ItemDataRole.UserRole, (entry["kind"], entry["key"]))
        self.empty.setVisible(not entries)
        self.list.setVisible(bool(entries))

    def _open_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, key = data
        self.open_requested.emit(str(kind), str(key))
        self.accept()

    def _clear(self) -> None:
        recently_viewed.clear(self._path)
        self.refresh()


__all__ = ["KIND_NAMES", "RecentlyViewedDialog"]
