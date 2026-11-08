"""The Qt command palette window.

The command list, the ranking and the mapping from a command to a destination
all live in :mod:`lgl.palette`, shared with the GTK frontend.  This module only
draws the search field and the rows, and calls back into the main window.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import palette as palette_lib

#: Pages that live in the main stack, whichever frontend is running.
_PAGE_KEYS = frozenset(key for key, _label in palette_lib.PAGES)

#: Commands that are really a button inside the Tools window for now.
_TOOLS_ACTIONS = frozenset(
    {"tools.open", "updates.check", "backup.create", "diagnostics.create"}
)

#: Where each row keeps its :class:`palette_lib.Command`.
COMMAND_ROLE = int(Qt.UserRole) + 1


class PaletteDialog(QDialog):
    """A modal search box over every page, setting and action."""

    def __init__(self, owner: Any = None) -> None:
        super().__init__(owner if isinstance(owner, QWidget) else None)
        self.setObjectName("sheet")
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(560, 420)
        self.setMinimumWidth(420)
        self._owner = owner
        self._items = palette_lib.commands()
        self._results: list[palette_lib.Command] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(14, 14, 14, 12)
        column.setSpacing(8)

        self.field = QLineEdit()
        self.field.setObjectName("paletteSearch")
        self.field.setPlaceholderText("Type a page, setting or command")
        self.field.textChanged.connect(self.refilter)
        self.field.installEventFilter(self)
        column.addWidget(self.field)

        self.results = QListWidget()
        self.results.setObjectName("paletteResults")
        self.results.setUniformItemSizes(True)
        self.results.itemActivated.connect(lambda _item: self.run_current())
        self.results.itemClicked.connect(lambda _item: self.run_current())
        column.addWidget(self.results, 1)

        self.empty = QLabel("Nothing matches that.")
        self.empty.setObjectName("settDesc")
        self.empty.hide()
        column.addWidget(self.empty)

        hint = QLabel("Enter runs the highlighted command · Escape closes")
        hint.setObjectName("settDesc")
        column.addWidget(hint)

        self.refilter("")

    # -- results ------------------------------------------------------------
    def refilter(self, text: str) -> None:
        """Show the commands matching ``text``, best first."""
        self._results = palette_lib.search(text, items=self._items)
        self.results.clear()
        for command in self._results:
            item = QListWidgetItem(command.label())
            item.setData(COMMAND_ROLE, command)
            if command.hint:
                item.setToolTip(command.hint)
            self.results.addItem(item)
        self.empty.setVisible(not self._results)
        if self._results:
            self.results.setCurrentRow(0)

    def current_command(self) -> palette_lib.Command | None:
        row = self.results.currentRow()
        return self._results[row] if 0 <= row < len(self._results) else None

    def move_selection(self, step: int) -> None:
        if not self._results:
            return
        row = max(0, min(len(self._results) - 1, self.results.currentRow() + step))
        self.results.setCurrentRow(row)

    # -- keys ---------------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        """Up/Down/Enter/Esc work while the search field has focus."""
        if watched is self.field and event.type() == QEvent.KeyPress:
            if self.handle_key(event.key()):
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.handle_key(event.key()):
            return
        super().keyPressEvent(event)

    def handle_key(self, key: int) -> bool:
        """Act on one key; ``False`` when the dialog does not use it."""
        if key == Qt.Key_Down:
            self.move_selection(1)
            return True
        if key == Qt.Key_Up:
            self.move_selection(-1)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.run_current()
            return True
        if key == Qt.Key_Escape:
            self.reject()
            return True
        return False

    # -- dispatch -----------------------------------------------------------
    def run_current(self) -> None:
        command = self.current_command()
        if command is not None:
            self.run(command)

    def run(self, command: palette_lib.Command) -> None:
        action = palette_lib.action_for(command) or ""
        owner = self._owner
        self.accept()
        if action.startswith("tab:"):
            page, _slash, tab = action[len("tab:"):].partition("/")
            show_place = getattr(owner, "show_place", None)
            if callable(show_place):
                show_place(page, tab)
            return
        if action in _PAGE_KEYS:
            show_view = getattr(owner, "show_view", None)
            if callable(show_view):
                show_view(action)
            return
        if action.startswith("settings."):
            show_settings = getattr(owner, "show_settings", None)
            if callable(show_settings):
                show_settings(action.split(".", 1)[1])
            return
        if action == "tags.edit":
            show_tags = getattr(owner, "show_tags", None)
            if callable(show_tags):
                show_tags()
            return
        method_name = "focus_search" if action == "search.focus" else None
        if action in _TOOLS_ACTIONS:
            method_name = "show_tools"
        method = getattr(owner, method_name, None) if method_name else None
        if callable(method):
            method()


def show_palette(owner: Any = None) -> PaletteDialog:
    """Open the palette over ``owner`` and keep it until a command runs."""
    dialog = PaletteDialog(owner)
    dialog.show()
    dialog.field.setFocus()
    return dialog


__all__ = ["COMMAND_ROLE", "PaletteDialog", "show_palette"]
