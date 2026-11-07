"""Switching between profiles: choose, create, rename or delete one."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
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

from .profiles import DEFAULT_ID, Profiles


class ProfilesDialog(QDialog):
    """The profile list with the active one marked."""

    def __init__(self, profiles: Profiles, parent: QWidget | None = None,
                 *, on_switch: Callable[[Any], None] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profiles")
        self.resize(420, 360)
        self.profiles = profiles
        self.on_switch = on_switch

        layout = QVBoxLayout(self)
        heading = QLabel("PROFILES", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(lambda *_: self.switch())
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        self.switch_btn = QPushButton("Use this profile", self)
        self.switch_btn.clicked.connect(self.switch)
        buttons.addWidget(self.switch_btn)
        self.new_btn = QPushButton("New…", self)
        self.new_btn.clicked.connect(self._new_clicked)
        buttons.addWidget(self.new_btn)
        self.rename_btn = QPushButton("Rename…", self)
        self.rename_btn.clicked.connect(self._rename_clicked)
        buttons.addWidget(self.rename_btn)
        self.delete_btn = QPushButton("Delete", self)
        self.delete_btn.clicked.connect(self._delete_clicked)
        buttons.addWidget(self.delete_btn)
        layout.addLayout(buttons)

        self.note = QLabel(
            "Each profile keeps its own settings, library, favourites and history. "
            "A switch is read at the next start; the last profile is never deleted, "
            "and no profile ever deletes game files.",
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

    def current_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(256) or "") if item is not None else ""

    def refresh(self) -> None:
        wanted = self.current_id() or self.profiles.active_id
        self.list.clear()
        active = self.profiles.active_id
        for profile in self.profiles.list():
            marker = "  ·  in use" if profile.id == active else ""
            item = QListWidgetItem(f"{profile.name}{marker}", self.list)
            item.setData(256, profile.id)
            if profile.id == wanted:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        has = bool(self.list.count())
        self.switch_btn.setEnabled(has)
        self.rename_btn.setEnabled(has)
        self.delete_btn.setEnabled(self.list.count() > 1)

    def switch(self) -> bool:
        profile_id = self.current_id()
        if not profile_id:
            return False
        changed = self.profiles.activate(profile_id)
        self.refresh()
        if changed:
            active = self.profiles.active()
            self.note.setText(
                f"Restart Lumora to switch to {active.name}. "
                "Its settings, library, favourites and history are used from then on."
            )
            if callable(self.on_switch):
                try:
                    self.on_switch(active)
                except Exception:  # noqa: BLE001 - a UI callback must not break a switch
                    pass
        return changed

    def create(self, name: str) -> bool:
        profile = self.profiles.create(name)
        self.refresh()
        return profile is not None

    def rename(self, name: str) -> bool:
        profile_id = self.current_id()
        changed = bool(profile_id) and self.profiles.rename(profile_id, name)
        self.refresh()
        return changed

    def delete(self) -> bool:
        profile_id = self.current_id()
        if not profile_id or profile_id == DEFAULT_ID:
            return False
        removed = self.profiles.remove(profile_id)
        self.refresh()
        return removed

    def _new_clicked(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Name")
        if ok and not self.create(name):
            QMessageBox.warning(self, "New profile", "That name is empty or already used.")

    def _rename_clicked(self) -> None:
        profile = self.profiles.get(self.current_id())
        if profile is None:
            return
        name, ok = QInputDialog.getText(self, "Rename profile", "Name", text=profile.name)
        if ok and not self.rename(name):
            QMessageBox.warning(self, "Rename profile", "That name is empty or already used.")

    def _delete_clicked(self) -> None:
        profile = self.profiles.get(self.current_id())
        if profile is None:
            return
        if profile.id == DEFAULT_ID:
            QMessageBox.information(
                self,
                "Delete profile",
                "The Default profile cannot be deleted: its folder holds this "
                "install's existing library and settings.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Delete profile",
            f"Delete {profile.name!r}?\n\nIts folder is kept on disk, and game "
            "files are never deleted.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete()


__all__ = ["ProfilesDialog"]
