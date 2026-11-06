"""The shelves editor: a saved filter that shows up as its own row on the home page."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import shelves as shelves_lib


class ShelvesDialog(QDialog):
    """Create a shelf from a genre, see what exists, and remove one."""

    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shelves")
        self.resize(460, 400)
        self.settings = settings

        layout = QVBoxLayout(self)
        heading = QLabel("HOME SHELVES", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)

        self.name = QLineEdit(self)
        self.name.setPlaceholderText("Shelf name, e.g. Co-op night")
        layout.addWidget(self.name)

        self.genre = QLineEdit(self)
        self.genre.setPlaceholderText("Genre to keep, e.g. RPG (blank keeps everything)")
        layout.addWidget(self.genre)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Titles shown", self))
        self.limit = QSpinBox(self)
        self.limit.setRange(1, 40)
        self.limit.setValue(shelves_lib.DEFAULT_LIMIT)
        size_row.addWidget(self.limit)
        size_row.addStretch(1)
        self.add_btn = QPushButton("Add shelf", self)
        self.add_btn.clicked.connect(self._add_clicked)
        size_row.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove", self)
        self.remove_btn.clicked.connect(self._remove_clicked)
        size_row.addWidget(self.remove_btn)
        layout.addLayout(size_row)

        self.note = QLabel(
            f"Up to {shelves_lib.MAX_SHELVES} shelves. The row appears above the Browse grid "
            "and is drawn from the loaded catalogue.",
            self,
        )
        self.note.setObjectName("detailNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for shelf in shelves_lib.shelves(self.settings):
            filters = shelf["filters"]
            genres = ", ".join(filters.genres) if filters.genres else "everything"
            item = QListWidgetItem(f"{shelf['name']}  ·  {genres}  ·  {shelf['limit']} titles")
            item.setData(256, shelf["id"])
            self.list.addItem(item)
        self.remove_btn.setEnabled(self.list.count() > 0)

    def current_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(256) or "") if item is not None else ""

    def add_shelf(self, name: str, genre: str = "") -> bool:
        text = str(genre or "").strip()
        filters = {"genres": [text]} if text else {}
        key = shelves_lib.add(self.settings, name, filters, self.limit.value())
        self.refresh()
        return bool(key)

    def remove_selected(self) -> bool:
        key = self.current_id()
        removed = bool(key) and shelves_lib.remove(self.settings, key)
        self.refresh()
        return removed

    def _add_clicked(self) -> None:
        if not self.add_shelf(self.name.text(), self.genre.text()):
            QMessageBox.warning(
                self,
                "Add shelf",
                "A shelf needs a name, and there is a limit of "
                f"{shelves_lib.MAX_SHELVES} shelves.",
            )
            return
        self.name.clear()
        self.genre.clear()

    def _remove_clicked(self) -> None:
        if not self.current_id():
            return
        answer = QMessageBox.question(self, "Remove shelf", "Remove the selected shelf?")
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_selected()


__all__ = ["ShelvesDialog"]
