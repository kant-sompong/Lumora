"""The Qt season comparison: two pickers, three lists, and why they are empty.

The data and the wording live in :mod:`lgl.season_data`; this file only draws
it.  Fetching runs through :func:`lgl.qt_widgets.run_async` so the dialog opens
at once, and tests pass ``threaded=False`` to keep everything synchronous.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from . import season_data
from .qt_widgets import run_async

#: ``(result key, heading)`` for the three lists, top to bottom.
SECTIONS = (
    ("only_first", "Only in A"),
    ("only_second", "Only in B"),
    ("shared", "In both (continuing)"),
)

ENTRY_ROLE = 256
HINT = "Select a title to see its score and episodes."


class SeasonCompareDialog(QDialog):
    """Compare what one anime season has against the next one."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        post: Any = None,
        cache_path: Any = None,
        threaded: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare seasons")
        self.resize(760, 760)
        self._post = post
        self._cache_path = cache_path
        self._threaded = threaded
        self._busy = False
        self._result = season_data.CompareResult()

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        heading = QLabel("SEASON COMPARISON", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        note = QLabel(
            "Two seasons from AniList, compared by series id: what only one "
            "season has and what carried over.",
            self,
        )
        note.setObjectName("detail-note")
        note.setWordWrap(True)
        layout.addWidget(note)

        season, year = season_data.latest_season()
        before, before_year = season_data.previous_season(season, year)
        self.season_a, self.year_a = self._pickers(layout, "A", season, year)
        self.season_b, self.year_b = self._pickers(layout, "B", before, before_year)

        buttons = QHBoxLayout()
        self.compare_btn = QPushButton("Compare", self)
        self.compare_btn.clicked.connect(lambda: self.compare())
        buttons.addWidget(self.compare_btn)
        self.status = QLabel("", self)
        self.status.setObjectName("detail-note")
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        layout.addLayout(buttons)

        self.summary = QLabel(season_data.season_compare.summary({}), self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.error = QLabel("", self)
        # A failure has to be readable, not another line of faint helper text.
        self.error.setStyleSheet("color: #e0a458;")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)

        self.lists: dict[str, QListWidget] = {}
        self.section_heads: dict[str, QLabel] = {}
        for key, title in SECTIONS:
            label = QLabel(title, self)
            label.setObjectName("filter-head")
            layout.addWidget(label)
            widget = QListWidget(self)
            widget.setMaximumHeight(130)
            widget.currentItemChanged.connect(self._on_selected)
            layout.addWidget(widget)
            self.lists[key] = widget
            self.section_heads[key] = label

        self.detail = QLabel(HINT, self)
        self.detail.setObjectName("detail-note")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        # Text lines keep their own height, and the stretch below only exists
        # while the three lists are gone, so a failure stays compact instead of
        # spreading the remaining lines down the dialog.
        for text_label in (heading, note, self.status, self.summary, self.error,
                           self.detail, *self.section_heads.values()):
            text_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._layout = layout
        self._tail = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self._tail_installed = False

        # Nothing is loaded until Compare is pressed, so no empty result box.
        for widget in (self.summary, self.error, self.detail):
            widget.setVisible(False)
        for widget in list(self.section_heads.values()) + list(self.lists.values()):
            widget.setVisible(False)

        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(close)
        layout.addLayout(foot)

    # -- pickers -----------------------------------------------------------
    def _pickers(self, layout: QVBoxLayout, side: str, season: str, year: int,
                 ) -> tuple[QComboBox, QComboBox]:
        row = QHBoxLayout()
        tag = QLabel(side, self)
        tag.setObjectName("detailEyebrow")
        row.addWidget(tag)
        seasons = QComboBox(self)
        for name in season_data.SEASONS:
            seasons.addItem(season_data.SEASON_NAMES[name], name)
        seasons.setCurrentIndex(max(0, season_data.SEASONS.index(season)))
        row.addWidget(seasons)
        years = QComboBox(self)
        current = season_data.latest_season()[1]
        for number in range(current + 1, current - 12, -1):
            years.addItem(str(number), number)
        index = years.findData(year)
        years.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(years)
        row.addStretch(1)
        layout.addLayout(row)
        return seasons, years

    def _compact(self, compact: bool) -> None:
        """Keep the lines at the top when the lists are not shown."""
        if compact and not self._tail_installed:
            self._layout.addItem(self._tail)
            self._tail_installed = True
        elif not compact and self._tail_installed:
            self._layout.removeItem(self._tail)
            self._tail_installed = False

    # -- data --------------------------------------------------------------
    def values(self) -> tuple[Any, Any, Any, Any]:
        return (
            self.season_a.currentData(),
            self.year_a.currentData(),
            self.season_b.currentData(),
            self.year_b.currentData(),
        )

    def compare(self) -> None:
        """Load both seasons and draw the comparison."""
        if self._busy:
            return
        self._busy = True
        self.compare_btn.setEnabled(False)
        self.status.setText("Loading both seasons…")
        self.error.setVisible(False)
        self.error.setText("")
        picked = self.values()

        def load() -> season_data.CompareResult:
            return season_data.compare(
                *picked, post=self._post, cache_path=self._cache_path
            )

        if not self._threaded:
            try:
                self.set_result(load())
            except Exception as exc:  # noqa: BLE001 - shown, never raised
                self.set_result(season_data.failed_compare(season_data.reason_of(exc)))
            return
        run_async(
            load,
            self.set_result,
            lambda error: self.set_result(season_data.failed_compare(error)),
        )

    def set_result(self, result: season_data.CompareResult) -> None:
        """Draw ``result`` without loading anything (tests and screenshots)."""
        self._busy = False
        self.compare_btn.setEnabled(True)
        self._result = result

        self.summary.setVisible(True)
        self.detail.setVisible(True)
        self.summary.setText(result.summary)
        error = result.error_text
        self.error.setText(error)
        self.error.setVisible(bool(error))
        self._compact(bool(error))

        for key, _title in SECTIONS:
            widget = self.lists[key]
            widget.clear()
            # A failure shows the error line and the controls, not three
            # empty boxes under their headings.
            widget.setVisible(not error)
            self.section_heads[key].setVisible(not error)
            for entry in getattr(result, key):
                item = QListWidgetItem(season_data.entry_line(entry))
                item.setData(ENTRY_ROLE, dict(entry))
                item.setToolTip(season_data.entry_details(entry))
                widget.addItem(item)

        notes: list[str] = []
        if result.saved_titles:
            notes.append("Showing saved titles from the last successful load.")
        if not (result.only_first or result.only_second or result.shared):
            notes.append("Nothing to compare yet.")
        self.status.setText(" ".join(notes))
        self.detail.setText(HINT)

    # -- selection ---------------------------------------------------------
    def _on_selected(self, current: QListWidgetItem | None, _previous: Any) -> None:
        entry = current.data(ENTRY_ROLE) if current is not None else None
        self.detail.setText(season_data.entry_details(entry) if entry else HINT)


def show_season_compare(parent: QWidget | None = None) -> SeasonCompareDialog:
    """Open the comparison from the app menu."""
    dialog = SeasonCompareDialog(parent)
    dialog.compare()
    dialog.exec()
    return dialog


__all__ = ["ENTRY_ROLE", "HINT", "SECTIONS", "SeasonCompareDialog", "show_season_compare"]
