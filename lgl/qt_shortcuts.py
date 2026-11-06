"""Rebinding the app's keyboard shortcuts."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from . import shortcut_prefs

#: ``(id, label, default)`` for every action the main window installs.
BINDINGS = (
    ("nav.browse", "Browse", "Ctrl+1"),
    ("nav.library", "Library", "Ctrl+2"),
    ("nav.downloads", "Downloads", "Ctrl+3"),
    ("nav.media", "Movies & TV", "Ctrl+4"),
    ("search", "Focus search", "Ctrl+F"),
    ("refresh", "Refresh", "Ctrl+R"),
    ("settings", "Settings", "Ctrl+,"),
)

DEFAULTS = {shortcut_id: keys for shortcut_id, _label, keys in BINDINGS}


class ShortcutsDialog(QDialog):
    """One editable key sequence per action, saved as overrides."""

    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(420, 320)
        self.settings = settings

        layout = QVBoxLayout(self)
        heading = QLabel("KEYBOARD SHORTCUTS", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for shortcut_id, label, _default in BINDINGS:
            field = QLineEdit(self)
            self.fields[shortcut_id] = field
            form.addRow(label, field)
        layout.addLayout(form)

        self.note = QLabel("Each action needs its own shortcut, e.g. Ctrl+Shift+L.", self)
        self.note.setObjectName("detailNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Reset
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._save_clicked)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self.reset)
        layout.addWidget(buttons)

        self.load()

    def load(self) -> None:
        current = shortcut_prefs.effective(self.settings, DEFAULTS)
        for shortcut_id, field in self.fields.items():
            field.setText(current.get(shortcut_id, DEFAULTS[shortcut_id]))

    def values(self) -> dict[str, str]:
        return {shortcut_id: field.text() for shortcut_id, field in self.fields.items()}

    def save(self) -> bool:
        wanted = self.values()
        if not all(str(value).strip() for value in wanted.values()):
            self.note.setText("Every action needs a shortcut.")
            return False
        if shortcut_prefs.conflicts(wanted):
            self.note.setText("Two actions cannot share a shortcut.")
            return False
        if not shortcut_prefs.set_bindings(self.settings, wanted):
            self.note.setText("These shortcuts could not be saved.")
            return False
        self.note.setText("Saved. The new keys apply the next time the window opens.")
        return True

    def reset(self) -> None:
        shortcut_prefs.reset(self.settings)
        self.load()
        self.note.setText("Restored the defaults.")

    def _save_clicked(self) -> None:
        if self.save():
            self.accept()


__all__ = ["BINDINGS", "DEFAULTS", "ShortcutsDialog"]
