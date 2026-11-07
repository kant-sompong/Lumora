"""The Qt collections editor: create a collection, rename it, and put library
titles in or take them out. Membership rules stay in :mod:`lgl.collections`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .collections import CollectionStore


def _title(entry: Any) -> str:
    return str(getattr(entry, "title", "") or getattr(entry, "identifier", "") or "Untitled")


class CollectionsDialog(QDialog):
    """The collection list on the left, its titles on the right."""

    def __init__(
        self,
        store: CollectionStore,
        entries: Sequence[Any],
        parent: QWidget | None = None,
        *,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.setWindowTitle("Collections")
        self.resize(620, 460)
        self.store = store
        self.entries = list(entries)

        layout = QVBoxLayout(self)
        heading = QLabel("COLLECTIONS", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        columns = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget(self)
        self.list.currentItemChanged.connect(lambda *_: self.refresh_members())
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.new_btn = QPushButton("New…", self)
        self.new_btn.clicked.connect(self._new_clicked)
        buttons.addWidget(self.new_btn)
        self.rename_btn = QPushButton("Rename…", self)
        self.rename_btn.clicked.connect(self._rename_clicked)
        buttons.addWidget(self.rename_btn)
        self.delete_btn = QPushButton("Delete", self)
        self.delete_btn.clicked.connect(self._delete_clicked)
        buttons.addWidget(self.delete_btn)
        left.addLayout(buttons)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        self.members = QListWidget(self)
        right.addWidget(self.members, 1)
        self.picker = QComboBox(self)
        for entry in self.entries:
            self.picker.addItem(_title(entry), getattr(entry, "identifier", ""))
        right.addWidget(self.picker)
        member_buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add title", self)
        self.add_btn.clicked.connect(self._add_clicked)
        member_buttons.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove", self)
        self.remove_btn.clicked.connect(self._remove_clicked)
        member_buttons.addWidget(self.remove_btn)
        member_buttons.addStretch(1)
        right.addLayout(member_buttons)
        columns.addLayout(right, 1)
        layout.addLayout(columns, 1)

        self.note = QLabel(
            "Collections are stored beside the library and never move or delete files.",
            self,
        )
        self.note.setObjectName("detailNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        if not self.embedded:
            footer = QHBoxLayout()
            footer.addStretch(1)
            close = QPushButton("Close", self)
            close.clicked.connect(self.accept)
            footer.addWidget(close)
            layout.addLayout(footer)

        self.refresh()

    # -- state ---------------------------------------------------------
    def current_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(256) or "") if item is not None else ""

    def refresh(self) -> None:
        """Rebuild the collection list, keeping the selection when possible."""
        wanted = self.current_id()
        self.list.clear()
        for collection in self.store.all():
            item = QListWidgetItem(f"{collection.name} ({len(collection.items)})", self.list)
            item.setData(256, collection.id)
            if collection.id == wanted:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.new_btn.setEnabled(True)
        has = bool(self.list.count())
        self.rename_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        self.refresh_members()

    def refresh_members(self) -> None:
        self.members.clear()
        collection_id = self.current_id()
        if not collection_id:
            return
        for entry in self.store.resolve(collection_id, self.entries):
            QListWidgetItem(_title(entry), self.members)
        self.add_btn.setEnabled(bool(self.entries) and bool(collection_id))
        self.remove_btn.setEnabled(self.members.count() > 0)

    # -- actions (also used by the tests) ------------------------------
    def create(self, name: str) -> bool:
        try:
            collection = self.store.create(name)
        except ValueError:
            return False
        self.refresh()
        for row in range(self.list.count()):
            if self.list.item(row).data(256) == collection.id:
                self.list.setCurrentRow(row)
                break
        return True

    def rename(self, name: str) -> bool:
        collection_id = self.current_id()
        if not collection_id:
            return False
        try:
            changed = self.store.rename(collection_id, name)
        except ValueError:
            return False
        self.refresh()
        return changed

    def delete(self) -> bool:
        collection_id = self.current_id()
        if not collection_id:
            return False
        removed = self.store.delete(collection_id)
        self.refresh()
        return removed

    def add(self, key: str) -> int:
        collection_id = self.current_id()
        if not collection_id:
            return 0
        added = self.store.add(collection_id, [key])
        self.refresh_members()
        return added

    def remove(self, key: str) -> int:
        collection_id = self.current_id()
        if not collection_id:
            return 0
        removed = self.store.remove(collection_id, [key])
        self.refresh_members()
        return removed

    # -- buttons -------------------------------------------------------
    def _new_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, "New collection", "Name")
        if ok and not self.create(name):
            QMessageBox.warning(self, "New collection", "That name is empty or already used.")

    def _rename_clicked(self) -> None:
        collection_id = self.current_id()
        collection = self.store.get(collection_id)
        if collection is None:
            return
        name, ok = QInputDialog.getText(self, "Rename collection", "Name", text=collection.name)
        if ok and not self.rename(name):
            QMessageBox.warning(self, "Rename collection", "That name is empty or already used.")

    def _delete_clicked(self) -> None:
        collection = self.store.get(self.current_id())
        if collection is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete collection",
            f"Delete {collection.name!r}? The titles themselves are kept.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete()

    def _add_clicked(self) -> None:
        self.add(str(self.picker.currentData() or ""))

    def _remove_clicked(self) -> None:
        item = self.members.currentItem()
        if item is None:
            return
        key = self._key_for(item.text())
        if key:
            self.remove(key)

    def _key_for(self, title: str) -> str:
        for entry in self.entries:
            if _title(entry) == title:
                return str(getattr(entry, "identifier", ""))
        return ""


__all__ = ["CollectionsDialog"]
