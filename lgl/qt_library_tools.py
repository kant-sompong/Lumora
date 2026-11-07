"""Qt screens for library housekeeping: tags, the detail editor, bulk edits and
duplicate groups.

Every rule lives in :mod:`lgl.library_tools`; these dialogs only collect input
and show what comes back, so the same behaviour can be shared with GTK later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import library_tools

#: Fields with a multi-line editor rather than a single line.
LONG_FIELDS = {"description"}


def as_entry(value: Any) -> dict[str, Any]:
    """A mapping view of a :class:`LibraryEntry`, a card or a plain dict."""
    if isinstance(value, Mapping):
        return dict(value)
    return {
        "key": getattr(value, "identifier", ""),
        "title": getattr(value, "title", ""),
        "description": getattr(value, "description", ""),
        "year": getattr(value, "year", ""),
        "poster": getattr(value, "cover_path", ""),
        "size": getattr(value, "size", 0),
        "favorite": bool(getattr(value, "favorite", False)),
        "genres": tuple(getattr(value, "genres", ()) or ()),
    }


class TagsDialog(QDialog):
    """Add and remove tags on one title, with the rest of the library counted."""

    def __init__(
        self,
        entries: Sequence[Any],
        settings: Any,
        parent: QWidget | None = None,
        *,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.setWindowTitle("Tags")
        self.resize(480, 420)
        self.settings = settings
        self.entries = [as_entry(entry) for entry in entries]

        layout = QVBoxLayout(self)
        heading = QLabel("TAGS", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        self.picker = QComboBox(self)
        for row in self.entries:
            self.picker.addItem(row["title"] or row["key"], row["key"])
        self.picker.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.picker)

        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)

        self.field = QLineEdit(self)
        self.field.setPlaceholderText("Add a tag, e.g. co-op")
        self.field.returnPressed.connect(self.add_current)
        layout.addWidget(self.field)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add tag", self)
        self.add_btn.clicked.connect(self.add_current)
        buttons.addWidget(self.add_btn)
        self.remove_btn = QPushButton("Remove tag", self)
        self.remove_btn.clicked.connect(self.remove_current)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch(1)
        self.close_btn = QPushButton("Close", self)
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setVisible(not embedded)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.counts = QLabel("", self)
        self.counts.setObjectName("detailNote")
        self.counts.setWordWrap(True)
        layout.addWidget(self.counts)

        self.refresh()

    # -- state ---------------------------------------------------------
    def current(self) -> dict[str, Any] | None:
        index = self.picker.currentIndex()
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def tags(self) -> tuple[str, ...]:
        entry = self.current()
        return library_tools.tags_for(self.settings, entry) if entry else ()

    def refresh(self) -> None:
        """Redraw the tag list and the library-wide counts."""
        self.list.clear()
        for tag in self.tags():
            QListWidgetItem(tag, self.list)
        entry = self.current()
        self.picker.setEnabled(bool(self.entries))
        self.field.setEnabled(entry is not None)
        self.add_btn.setEnabled(entry is not None)
        self.remove_btn.setEnabled(entry is not None)
        counts = library_tools.tag_counts(self.entries, self.settings)
        self.counts.setText(
            "In this library: " + (" · ".join(f"{tag} {count}" for tag, count in counts.items()) if counts else "no tags yet")
        )

    # -- actions -------------------------------------------------------
    def add_current(self) -> None:
        entry = self.current()
        if entry is None:
            return
        text = self.field.text()
        if not library_tools.clean_tag(text):
            return
        library_tools.add_tag(self.settings, entry, text)
        self.field.clear()
        self.refresh()

    def remove_current(self) -> None:
        entry = self.current()
        item = self.list.currentItem()
        if entry is None or item is None:
            return
        library_tools.remove_tag(self.settings, entry, item.text())
        self.refresh()


class MetadataEditorDialog(QDialog):
    """Correct a title's name, description, year or poster by hand."""

    def __init__(self, entry: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit details")
        self.resize(520, 420)
        self.entry = as_entry(entry)

        layout = QVBoxLayout(self)
        heading = QLabel("EDIT DETAILS", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        form = QFormLayout()
        self.fields: dict[str, QWidget] = {}
        for name, label in library_tools.EDITABLE:
            value = str(self.entry.get(name, "") or "")
            if name in LONG_FIELDS:
                widget: QWidget = QPlainTextEdit(value, self)
                widget.setMinimumHeight(120)
            else:
                widget = QLineEdit(value, self)
                if name == "year":
                    widget.setPlaceholderText("e.g. 1998")
                elif name == "poster":
                    widget.setPlaceholderText("Path or URL to a poster image")
            self.fields[name] = widget
            form.addRow(label, widget)
        layout.addLayout(form)

        self.note = QLabel(
            "Blank fields are kept as they are; a year that is not a number is ignored.",
            self,
        )
        self.note.setObjectName("detailNote")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name, widget in self.fields.items():
            if isinstance(widget, QPlainTextEdit):
                values[name] = widget.toPlainText()
            else:
                values[name] = widget.text()
        return values

    def edited(self) -> dict[str, Any]:
        """The entry with every field normalised, ready to be stored."""
        result = dict(self.entry)
        for name, value in self.values().items():
            result = library_tools.apply_edit(result, name, value)
        return result

    def changed(self) -> bool:
        edited = self.edited()
        return any(
            str(edited.get(name) or "") != str(self.entry.get(name) or "")
            for name, _label in library_tools.EDITABLE
        )


class BulkEditDialog(QDialog):
    """Set one field on several titles at once."""

    def __init__(
        self,
        entries: Sequence[Any],
        keys: Sequence[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bulk edit")
        self.resize(420, 240)
        self.entries = [as_entry(entry) for entry in entries]
        self.keys = [str(key) for key in keys]

        layout = QVBoxLayout(self)
        heading = QLabel(f"BULK EDIT · {len(self.keys)} SELECTED", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        form = QFormLayout()
        self.field = QComboBox(self)
        for name, label in library_tools.EDITABLE:
            self.field.addItem(label, name)
        self.field.currentIndexChanged.connect(self._sync_input)
        form.addRow("Field", self.field)
        self.value = QLineEdit(self)
        form.addRow("New value", self.value)
        layout.addLayout(form)

        self._sync_input()
        layout.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def field_name(self) -> str:
        return str(self.field.currentData())

    def _sync_input(self) -> None:
        self.value.setPlaceholderText("1998" if self.field_name() == "year" else "Text for every chosen title")

    def result_entries(self) -> tuple[list[dict[str, Any]], int]:
        return library_tools.bulk_update(self.entries, self.keys, self.field_name(), self.value.text())


class DuplicatesDialog(QDialog):
    """Titles that look like the same thing, and where they live."""

    def __init__(self, entries: Sequence[Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Possible duplicates")
        self.resize(560, 420)
        self.groups = library_tools.duplicates([as_entry(entry) for entry in entries])

        layout = QVBoxLayout(self)
        heading = QLabel("POSSIBLE DUPLICATES", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        self.list = QListWidget(self)
        for group in self.groups:
            title = str(group[0].get("title") or group[0].get("key") or "Untitled")
            size = group[0].get("size")
            detail = f"{title} · {len(group)} copies"
            if size:
                detail += f" · {int(size)} bytes each"
            item = QListWidgetItem(detail, self.list)
            item.setToolTip("\n".join(str(row.get("key") or "") for row in group))
        layout.addWidget(self.list, 1)

        self.empty = QLabel("No duplicates found.", self)
        self.empty.setObjectName("detailNote")
        self.empty.setVisible(not self.groups)
        layout.addWidget(self.empty)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


__all__ = [
    "BulkEditDialog",
    "DuplicatesDialog",
    "LONG_FIELDS",
    "MetadataEditorDialog",
    "TagsDialog",
    "as_entry",
]
