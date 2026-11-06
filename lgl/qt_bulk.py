"""Bulk selection and actions for the Qt library page.

The rules live in :mod:`lgl.library_tools` — the same plans the GTK bar writes —
so this module only draws the bar, asks for input and hands the plan to the
stores the page already has.  Nothing here renames, moves or deletes a file.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import library_tools, platform_support
from .collections import COLLECTIONS_FILENAME, CollectionStore


def _button(text: str, callback: Any, tooltip: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("small")
    button.setCursor(Qt.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    button.clicked.connect(callback)
    return button


class QtBulk:
    """The Select toggle, the “N selected” bar and the actions it runs."""

    def __init__(self, page: Any, *, collections_store: Any = None) -> None:
        self.page = page
        self._active = False
        self._selected: dict[str, bool] = {}
        self._checks: dict[str, QCheckBox] = {}
        self.history = library_tools.BulkHistory()
        self._collections: CollectionStore | None = collections_store

        self.toggle = QPushButton("Select")
        self.toggle.setObjectName("pill")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setToolTip("Select several games, then act on all of them")
        self.toggle.toggled.connect(self.set_active)

        self.bar = QWidget()
        self.bar.setObjectName("bulkBar")
        outer = QVBoxLayout(self.bar)
        outer.setContentsMargins(24, 2, 24, 8)
        outer.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.count = QLabel("No games selected")
        self.count.setObjectName("bulkCount")
        row.addWidget(self.count)
        self.select_all_button = _button(
            "Select all", self.select_all, "Select every game the page is showing"
        )
        row.addWidget(self.select_all_button)
        self.clear_button = _button("Clear", self.clear, "Clear the selection")
        row.addWidget(self.clear_button)
        divider = QLabel("·")
        divider.setObjectName("bulkDivider")
        row.addWidget(divider)
        self.tag_button = _button("Tag…", self.run_tag, "Add or remove one tag")
        row.addWidget(self.tag_button)
        self.collection_button = _button(
            "Collection…", self.run_collection, "Add the selection to a collection"
        )
        row.addWidget(self.collection_button)
        self.favorite_button = _button(
            "Favorite", lambda _checked=False: self.run_favorite(True),
            "Mark the selection as favorite",
        )
        row.addWidget(self.favorite_button)
        self.unfavorite_button = _button(
            "Unfavorite", lambda _checked=False: self.run_favorite(False),
            "Remove the favorite mark",
        )
        row.addWidget(self.unfavorite_button)
        self.field_button = _button(
            "Edit field…", self.run_field, "Set one metadata field on the selection"
        )
        row.addWidget(self.field_button)
        self.undo_button = _button("Undo", self.undo, "Undo the last bulk action")
        row.addWidget(self.undo_button)
        self.done_button = _button(
            "Done", lambda _checked=False: self.set_active(False), "Leave selection mode"
        )
        row.addWidget(self.done_button)
        outer.addLayout(row)

        self.report = QLabel("")
        self.report.setObjectName("bulkReport")
        self.report.setWordWrap(True)
        outer.addWidget(self.report)
        self.bar.hide()

        self._actions = (
            self.tag_button,
            self.collection_button,
            self.favorite_button,
            self.unfavorite_button,
            self.field_button,
        )
        self._sync_controls()

    # -- the stores the page already has --------------------------------------
    def _cards(self) -> dict[str, Any]:
        return dict(getattr(self.page, "_cards", None) or {})

    def entries(self) -> list[Any]:
        return list(getattr(self.page, "_entries", None) or [])

    def collection_store(self) -> CollectionStore:
        if self._collections is None:
            self._collections = CollectionStore(
                platform_support.data_dir() / COLLECTIONS_FILENAME
            )
        return self._collections

    # -- selection -----------------------------------------------------------
    def keys(self) -> list[str]:
        """Selected identifiers, in the order they were picked."""
        return [key for key, on in self._selected.items() if on]

    def is_selected(self, key: Any) -> bool:
        return bool(self._selected.get(str(key or "")))

    def set_selected(self, key: Any, on: bool) -> None:
        text = str(key or "")
        if not text:
            return
        self._selected[text] = bool(on)
        check = self._checks.get(text)
        if check is not None and check.isChecked() != bool(on):
            check.setChecked(bool(on))
        self._sync_controls()

    def select_all(self, _ignored: Any = None) -> int:
        for key in self._cards():
            self._selected[str(key)] = True
        self.sync()
        return len(self.keys())

    def clear(self, _ignored: Any = None) -> None:
        self._selected.clear()
        for check in self._checks.values():
            check.setChecked(False)
        self._sync_controls()

    def _on_check(self, key: str, on: bool) -> None:
        self._selected[key] = bool(on)
        self._sync_controls()

    # -- selection mode ------------------------------------------------------
    def set_active(self, on: bool, _ignored: Any = None) -> bool:
        """Turn selection mode on or off; returns whether it is on."""
        self._active = bool(on)
        if self.toggle.isChecked() != self._active:
            self.toggle.setChecked(self._active)
        self.bar.setVisible(self._active)
        if self._active:
            self.sync()
        else:
            self._clear_checks()
            self._selected.clear()
            self._report("")
        self._sync_controls()
        return self._active

    @property
    def active(self) -> bool:
        return self._active

    def sync(self) -> int:
        """Put one readable check box inside every tile the page is showing.

        Grid cards get it between the cover art and the title, list rows get it
        in front of the text: neither puts it on top of the artwork.
        """
        if not self._active:
            return 0
        self._clear_checks()
        for key, card in self._cards().items():
            layout = card.layout()
            if layout is None:
                continue
            check = QCheckBox()
            check.setObjectName("tileCheck")
            check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            check.setFixedSize(22, 20)
            check.setCursor(Qt.PointingHandCursor)
            check.setToolTip("Select this game")
            check.setChecked(self.is_selected(key))
            check.toggled.connect(lambda on, k=str(key): self._on_check(k, bool(on)))
            layout.insertWidget(1 if hasattr(card, "cover") else 0, check)
            check.show()
            self._checks[str(key)] = check
        self._sync_controls()
        return len(self._checks)

    def _clear_checks(self) -> None:
        for check in self._checks.values():
            check.setParent(None)
            check.deleteLater()
        self._checks = {}

    def _sync_controls(self) -> None:
        count = len(self.keys())
        self.count.setText(f"{count} selected" if count else "No games selected")
        for widget in self._actions:
            widget.setEnabled(count > 0)
        self.select_all_button.setEnabled(bool(self._cards()))
        self.clear_button.setEnabled(count > 0)
        self.undo_button.setEnabled(self.history.can_undo())

    # -- input ---------------------------------------------------------------
    def _shell(self, title: str, intro: str) -> tuple[QDialog, QVBoxLayout, QHBoxLayout]:
        dialog = QDialog(self.page)
        dialog.setObjectName("sheet")
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(440)
        column = QVBoxLayout(dialog)
        column.setContentsMargins(20, 18, 20, 16)
        column.setSpacing(8)
        heading = QLabel(intro)
        heading.setObjectName("detailNote")
        heading.setWordWrap(True)
        column.addWidget(heading)
        column.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        row.addWidget(cancel)
        return dialog, column, row

    def ask_tag(self) -> tuple[str, bool] | None:
        """Ask for a tag and whether to add or remove it."""
        count = len(self.keys())
        dialog, column, row = self._shell(
            "Bulk tag",
            f"{count} game{'s' if count != 1 else ''} selected. One tag is added to "
            "all of them, or removed from all of them.",
        )
        mode = QComboBox()
        mode.addItem("Add tag", "add")
        mode.addItem("Remove tag", "remove")
        column.addWidget(mode)
        entry = QLineEdit()
        entry.setPlaceholderText("Tag, e.g. co-op")
        column.addWidget(entry)
        ok = QPushButton("Continue")
        ok.setObjectName("suggested")
        ok.setDefault(True)
        ok.clicked.connect(dialog.accept)
        row.addWidget(ok)
        column.addLayout(row)
        entry.setFocus()
        if dialog.exec() != QDialog.Accepted:
            return None
        tag = entry.text()
        if not library_tools.clean_tag(tag):
            return None
        return tag, mode.currentData() == "remove"

    def ask_collection(self) -> str | None:
        """Ask which collection the selection joins."""
        store = self.collection_store()
        names = [collection.name for collection in store.all()]
        count = len(self.keys())
        dialog, column, row = self._shell(
            "Bulk collection",
            f"Add {count} game{'s' if count != 1 else ''} to a collection. Type a new "
            "name or an existing one.",
        )
        entry = QLineEdit()
        entry.setPlaceholderText("Collection name")
        column.addWidget(entry)
        hint = QLabel(
            "Existing collections: " + ", ".join(names)
            if names
            else "There are no collections yet; typing a name makes one."
        )
        hint.setObjectName("detailNote")
        hint.setWordWrap(True)
        column.addWidget(hint)
        ok = QPushButton("Continue")
        ok.setObjectName("suggested")
        ok.setDefault(True)
        ok.clicked.connect(dialog.accept)
        row.addWidget(ok)
        column.addLayout(row)
        entry.setFocus()
        if dialog.exec() != QDialog.Accepted:
            return None
        name = " ".join(entry.text().split())[:80]
        return name or None

    def ask_field(self) -> tuple[str, str] | None:
        """Ask which metadata field to set, and to what."""
        count = len(self.keys())
        dialog, column, row = self._shell(
            "Bulk edit",
            f"Set one metadata field on {count} game{'s' if count != 1 else ''}. Only "
            "the library index changes; files on disk are untouched.",
        )
        picker = QComboBox()
        for name, label_text in library_tools.BULK_FIELDS:
            picker.addItem(label_text, name)
        column.addWidget(picker)
        entry = QLineEdit()
        entry.setPlaceholderText("New value, e.g. 1998 or co-op")
        column.addWidget(entry)
        ok = QPushButton("Continue")
        ok.setObjectName("suggested")
        ok.setDefault(True)
        ok.clicked.connect(dialog.accept)
        row.addWidget(ok)
        column.addLayout(row)
        entry.setFocus()
        if dialog.exec() != QDialog.Accepted:
            return None
        return picker.currentData() or "year", entry.text()

    def confirm(self, text: str) -> bool:
        """Ask before writing anything; tests replace this."""
        answer = QMessageBox.question(
            self.page,
            "Bulk action",
            text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    # -- actions -------------------------------------------------------------
    def _tags_of(self, entry: Any) -> tuple[str, ...]:
        return library_tools.tags_for(getattr(self.page, "settings", None), entry)

    def run_tag(self, _ignored: Any = None) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        answer = self.ask_tag()
        if answer is None:
            return None
        tag, remove = answer
        plan = library_tools.plan_tags(
            self.entries(), self.keys(), tag, tags_of=self._tags_of, remove=remove
        )
        return self.finish(plan)

    def run_collection(self, _ignored: Any = None) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        name = self.ask_collection()
        if name is None:
            return None
        store = self.collection_store()
        existing = store.by_name(name)
        collection_id = existing.id if existing is not None else ""
        members = set(store.members(collection_id)) if collection_id else set()
        plan = library_tools.plan_collection(
            self.entries(),
            self.keys(),
            name,
            collection_id=collection_id,
            is_member=lambda entry: str(getattr(entry, "identifier", "")) in members,
        )
        return self.finish(plan)

    def run_favorite(
        self, value: bool, _ignored: Any = None
    ) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        plan = library_tools.plan_favorite(self.entries(), self.keys(), value)
        return self.finish(plan)

    def run_field(self, _ignored: Any = None) -> library_tools.BulkPlan | None:
        if not self.keys():
            self.show_report("Select at least one game first.")
            return None
        answer = self.ask_field()
        if answer is None:
            return None
        field_name, value = answer
        plan = library_tools.plan_field(self.entries(), self.keys(), field_name, value)
        return self.finish(plan)

    def finish(self, plan: library_tools.BulkPlan) -> library_tools.BulkPlan:
        """Confirm, write, remember for undo, then say what happened."""
        if not plan.changes:
            self.show_report(plan.summary())
            return plan
        if not self.confirm(plan.confirm()):
            self.show_report("Cancelled; nothing changed.")
            return plan
        written = self.write(plan)
        self.history.remember(written)
        self.show_report(written.summary())
        self._refresh()
        return written

    def write(self, plan: library_tools.BulkPlan) -> library_tools.BulkPlan:
        """Write a plan through the page's stores; returns what was written."""
        if plan.change.action == "collection" and not plan.change.field:
            store = self.collection_store()
            existing = store.by_name(plan.change.value)
            collection = existing if existing is not None else store.create(plan.change.value)
            plan = replace(
                plan,
                change=replace(plan.change, field=collection.id),
                created_collection="" if existing is not None else collection.id,
            )
        library_tools.apply_plan(
            plan,
            settings=getattr(self.page, "settings", None),
            store=getattr(self.page, "store", None),
            collections_store=self.collection_store(),
        )
        return plan

    def undo(self, _ignored: Any = None) -> None:
        plan = self.history.undo()
        if plan is None:
            self.show_report("Nothing to undo.")
            return
        written = self.write(plan)
        self.show_report(f"Undone: {written.summary()}")
        self._refresh()

    # -- output --------------------------------------------------------------
    def show_report(self, text: str) -> None:
        self._report(text)
        toast = getattr(self.page, "_toast", None)
        if callable(toast):
            toast(text)

    def _report(self, text: str) -> None:
        self.report.setText(text)
        self.report.setVisible(bool(text))

    def _refresh(self) -> None:
        store = getattr(self.page, "store", None)
        setter = getattr(self.page, "set_entries", None)
        if store is not None and callable(setter):
            setter(store.entries())
        else:
            self.page.refresh()
        self.sync()


__all__ = ["QtBulk"]
