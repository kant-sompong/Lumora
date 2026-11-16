"""The "Which part?" sheet shown when a TMDB title maps to several AniList entries.

The mapping list cannot always tell one entry from another: a show split into
cour seasons, a compilation film, a remake.  Rather than guess, the play path
asks.  The dialog is an ordinary sheet - the same surface, padding, gap and
default-button rules as every other dialog, via ``style_dialog`` - and it only
names the choices it was given: an id whose ani.pm title has not arrived yet is
listed as "Part 1", "Part 2" and so on.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from .qt_widgets import style_dialog

#: What a candidate is called before, or instead of, its ani.pm title.
PART_FALLBACK = "Part {number}"


def candidate_label(index: int, title: str = "") -> str:
    """``Part 2`` or ``Part 2 · Title``; the number is one based."""
    fallback = PART_FALLBACK.format(number=index + 1)
    text = " ".join(str(title or "").split())
    return f"{fallback} · {text}" if text else fallback


class WhichPartDialog(QDialog):
    """One button per candidate; ``picked`` is the chosen target, or None."""

    def __init__(
        self,
        show_title: str,
        candidates: Sequence[object],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Which part?")
        self.setModal(True)
        self.picked: object | None = None
        self.candidates = list(candidates)
        self._titles: dict[int, str] = {}
        self._buttons: list[QPushButton] = []

        column = QVBoxLayout(self)
        heading = QLabel("Which part?")
        heading.setObjectName("pageTitle")
        column.addWidget(heading)
        note = QLabel(
            ("“" + show_title + "” matches more than one entry on ani.pm. "
             "Choose the one this episode belongs to.")
            if str(show_title or "").strip()
            else "This title matches more than one entry on ani.pm. Choose one."
        )
        note.setObjectName("detailNote")
        note.setWordWrap(True)
        column.addWidget(note)

        for index in range(len(self.candidates)):
            button = QPushButton(candidate_label(index))
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip("Play this part")
            button.clicked.connect(
                lambda _checked=False, position=index: self._choose(position)
            )
            self._buttons.append(button)
            column.addWidget(button)

        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        column.addWidget(cancel)
        style_dialog(self, first=self._buttons[0] if self._buttons else cancel)

    # -- labels -------------------------------------------------------------

    def labels(self) -> list[str]:
        return [
            candidate_label(index, self._titles.get(index, ""))
            for index in range(len(self.candidates))
        ]

    def set_titles(self, titles: Sequence[str]) -> None:
        """Fill in the entries ani.pm named, in candidate order.

        Called on the UI thread once the lookups come back; a missing or empty
        title keeps that row's ``Part N`` fallback instead of inventing one.
        """
        for index, title in enumerate(titles):
            text = " ".join(str(title or "").split())
            if text:
                self._titles[index] = text
        for index, button in enumerate(self._buttons):
            button.setText(candidate_label(index, self._titles.get(index, "")))

    # -- choice -------------------------------------------------------------

    def _choose(self, index: int) -> None:
        if 0 <= index < len(self.candidates):
            self.picked = self.candidates[index]
        self.accept()

    def choice(self) -> object | None:
        return self.picked

    def buttons(self) -> list[QPushButton]:
        return list(self._buttons)


__all__ = ["PART_FALLBACK", "WhichPartDialog", "candidate_label"]
