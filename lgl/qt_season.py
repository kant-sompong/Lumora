"""The Qt season comparison card: two pickers, then three short columns.

The data and the wording live in :mod:`lgl.season_data`; this file only draws
it.  Fetching runs through :func:`lgl.qt_widgets.run_async`, and tests pass
``threaded=False`` to keep everything synchronous.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import season_data
from .qt_feed import FeedRow
from .qt_widgets import clear_layout, run_async

#: Dot colours for the three counts.  The label text says which count it is, so
#: colour is never the only signal.
COUNT_COLOURS = ("#5a9bd4", "#d09a3c", "#78a86a")


class CompareCard(QWidget):
    """A compact Compare seasons card: two pickers, then three short columns.

    Nothing is fetched until Compare runs, so the card is one picker row until
    then.  A failure is one dim line and a Retry, never three empty boxes, and a
    row opens the title's own detail page.
    """

    item_clicked = Signal(object)

    TITLE = "Compare seasons"
    SUBTITLE = ("See what is new, what left and what carried over between two "
                "seasons.")
    LIMIT = 5
    #: Below this width the three columns stack instead of sitting side by side.
    NARROW = 760
    SAME_HINT = "Pick two different seasons."
    COLUMNS = ("only_first", "only_second", "shared")
    #: Embedded in a page, so there is no window for it to close.
    close_btn = None

    def __init__(self, parent: QWidget | None = None, *, loader: Any = None,
                 threaded: bool = True, cache_path: Any = None,
                 years: int = 12) -> None:
        super().__init__(parent)
        self.setObjectName("compareCard")
        # The card is as tall as its lines: on a page that hands out extra
        # height (the detail panel does) the pickers stay at the top instead of
        # spreading apart.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._loader = loader
        self._threaded = threaded
        self._cache_path = cache_path
        self._busy = False
        self._token = 0
        self._expanded = {key: False for key in self.COLUMNS}
        self._result: season_data.CompareResult | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self.title = QLabel(self.TITLE, self)
        self.title.setObjectName("sectionTitle")
        column.addWidget(self.title)
        self.subtitle = QLabel(self.SUBTITLE, self)
        self.subtitle.setObjectName("mediaNote")
        self.subtitle.setWordWrap(True)
        column.addWidget(self.subtitle)

        # Two presets, so the usual comparisons take one click.
        self.chips = QWidget(self)
        chip_row = QHBoxLayout(self.chips)
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        self.chip_last = QPushButton("This vs last season", self.chips)
        self.chip_last.setObjectName("compareChip")
        self.chip_last.setCursor(Qt.PointingHandCursor)
        self.chip_last.clicked.connect(lambda: self.use_last_season())
        chip_row.addWidget(self.chip_last)
        self.chip_year = QPushButton("This vs same season last year", self.chips)
        self.chip_year.setObjectName("compareChip")
        self.chip_year.setCursor(Qt.PointingHandCursor)
        self.chip_year.clicked.connect(lambda: self.use_same_season_last_year())
        chip_row.addWidget(self.chip_year)
        chip_row.addStretch(1)
        column.addWidget(self.chips)

        # The one row that is always there: A, swap, B, Compare.
        self.picker_row = QWidget(self)
        pickers = QHBoxLayout(self.picker_row)
        pickers.setContentsMargins(0, 0, 0, 0)
        pickers.setSpacing(8)
        self.combo_a = self._picker(self.picker_row, "compareA")
        pickers.addWidget(self.combo_a, 1)
        self.swap_btn = QPushButton("Swap", self.picker_row)
        self.swap_btn.setObjectName("compareSwap")
        self.swap_btn.setToolTip("Swap the two seasons")
        self.swap_btn.setCursor(Qt.PointingHandCursor)
        self.swap_btn.clicked.connect(lambda: self.swap())
        pickers.addWidget(self.swap_btn, 0)
        self.combo_b = self._picker(self.picker_row, "compareB")
        pickers.addWidget(self.combo_b, 1)
        self.compare_btn = QPushButton("Compare", self.picker_row)
        self.compare_btn.setObjectName("compareRun")
        self.compare_btn.setCursor(Qt.PointingHandCursor)
        self.compare_btn.clicked.connect(lambda: self.compare())
        pickers.addWidget(self.compare_btn, 0)
        column.addWidget(self.picker_row)

        self.hint = QLabel(self.SAME_HINT, self)
        self.hint.setObjectName("mediaNote")
        self.hint.setVisible(False)
        column.addWidget(self.hint)

        self.loading = QLabel("", self)
        self.loading.setObjectName("mediaNote")
        self.loading.setVisible(False)
        column.addWidget(self.loading)

        self.error_row = QWidget(self)
        error_line = QHBoxLayout(self.error_row)
        error_line.setContentsMargins(0, 0, 0, 0)
        error_line.setSpacing(8)
        self.error = QLabel("", self.error_row)
        self.error.setObjectName("compareError")
        self.error.setWordWrap(True)
        self.retry = QPushButton("Retry", self.error_row)
        self.retry.setObjectName("compareRetry")
        self.retry.setCursor(Qt.PointingHandCursor)
        self.retry.clicked.connect(lambda: self.compare())
        error_line.addWidget(self.error, 1)
        error_line.addWidget(self.retry, 0)
        self.error_row.setVisible(False)
        column.addWidget(self.error_row)

        self.summary = QWidget(self)
        summary_row = QHBoxLayout(self.summary)
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(14)
        self.count_labels: list[QLabel] = []
        for colour in COUNT_COLOURS:
            host = QWidget(self.summary)
            line = QHBoxLayout(host)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(5)
            dot = QLabel("\u25cf", host)
            dot.setStyleSheet(f"color: {colour};")
            line.addWidget(dot, 0)
            text = QLabel("", host)
            text.setObjectName("mediaNote")
            line.addWidget(text, 0)
            line.addStretch(1)
            summary_row.addWidget(host, 1)
            self.count_labels.append(text)
        self.summary.setVisible(False)
        column.addWidget(self.summary)

        self.columns_host = QWidget(self)
        self._columns_row = QBoxLayout(QBoxLayout.LeftToRight, self.columns_host)
        self._columns_row.setContentsMargins(0, 0, 0, 0)
        self._columns_row.setSpacing(14)
        self._column_frames: dict[str, QFrame] = {}
        self._column_heads: dict[str, QLabel] = {}
        self._column_rows: dict[str, QVBoxLayout] = {}
        self._column_buttons: dict[str, QPushButton] = {}
        for key in self.COLUMNS:
            frame = QFrame(self.columns_host)
            frame.setObjectName("feedColumn")
            box = QVBoxLayout(frame)
            box.setContentsMargins(12, 10, 12, 10)
            box.setSpacing(4)
            head = QLabel("", frame)
            head.setObjectName("detailEyebrow")
            box.addWidget(head)
            host = QWidget(frame)
            rows = QVBoxLayout(host)
            rows.setContentsMargins(0, 0, 0, 0)
            rows.setSpacing(2)
            box.addWidget(host)
            more = QPushButton("", frame)
            more.setObjectName("compareMore")
            more.setCursor(Qt.PointingHandCursor)
            more.setVisible(False)
            more.clicked.connect(lambda _=False, k=key: self.toggle_column(k))
            box.addWidget(more)
            box.addStretch(1)
            frame.setVisible(False)
            self._columns_row.addWidget(frame, 1)
            self._column_frames[key] = frame
            self._column_heads[key] = head
            self._column_rows[key] = rows
            self._column_buttons[key] = more
        self.columns_host.setVisible(False)
        column.addWidget(self.columns_host)
        column.addStretch(1)

        self._fill_choices(years)
        self._set_defaults()
        for combo in (self.combo_a, self.combo_b):
            combo.currentIndexChanged.connect(lambda _index=0: self._update_state())
        self._update_state()

    # -- pickers ------------------------------------------------------------
    def _picker(self, parent: QWidget, name: str) -> QComboBox:
        combo = QComboBox(parent)
        combo.setObjectName(name)
        combo.setMinimumWidth(150)
        combo.setCursor(Qt.PointingHandCursor)
        combo.setToolTip("Season and year to compare")
        return combo

    def _fill_choices(self, years: int) -> None:
        season, year = season_data.latest_season()
        choices: list[tuple[str, int]] = []
        for _ in range(max(2, int(years))):
            choices.append((season, year))
            season, year = season_data.previous_season(season, year)
        for combo in (self.combo_a, self.combo_b):
            combo.blockSignals(True)
            combo.clear()
            for name, number in choices:
                combo.addItem(season_data.season_label(name, number), (name, number))
            combo.blockSignals(False)

    def _set_defaults(self) -> None:
        """The current season against the previous one."""
        if self.combo_a.count():
            self.combo_a.setCurrentIndex(0)
        if self.combo_b.count() > 1:
            self.combo_b.setCurrentIndex(1)
        elif self.combo_b.count():
            self.combo_b.setCurrentIndex(0)

    @staticmethod
    def _chosen(combo: QComboBox) -> tuple[str, int]:
        data = combo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            try:
                return str(data[0]), int(data[1])
            except (TypeError, ValueError):
                return "", 0
        return "", 0

    def values(self) -> tuple[str, int, str, int]:
        season_a, year_a = self._chosen(self.combo_a)
        season_b, year_b = self._chosen(self.combo_b)
        return season_a, year_a, season_b, year_b

    def labels(self) -> tuple[str, str]:
        season_a, year_a, season_b, year_b = self.values()
        return (season_data.season_label(season_a, year_a),
                season_data.season_label(season_b, year_b))

    def same_selection(self) -> bool:
        season_a, year_a, season_b, year_b = self.values()
        return bool(season_a) and (season_a, year_a) == (season_b, year_b)

    def swap(self) -> None:
        """Flip A and B."""
        index_a = self.combo_a.currentIndex()
        index_b = self.combo_b.currentIndex()
        self.combo_a.setCurrentIndex(index_b)
        self.combo_b.setCurrentIndex(index_a)
        self._update_state()

    def use_last_season(self) -> None:
        self._use_pair(1)

    def use_same_season_last_year(self) -> None:
        """This season against the same season a year earlier."""
        self._use_pair(4)

    def _use_pair(self, offset: int) -> None:
        self.combo_a.blockSignals(True)
        self.combo_b.blockSignals(True)
        self.combo_a.setCurrentIndex(0)
        self.combo_b.setCurrentIndex(max(0, min(self.combo_b.count() - 1, offset)))
        self.combo_a.blockSignals(False)
        self.combo_b.blockSignals(False)
        self._update_state()

    def _update_state(self) -> None:
        same = self.same_selection()
        self.hint.setVisible(same)
        self.compare_btn.setEnabled(not same and not self._busy)

    # -- comparing ----------------------------------------------------------
    def set_loading(self, label_a: str = "", label_b: str = "") -> None:
        """One dim line while both seasons load."""
        self.loading.setText(f"Comparing {label_a} and {label_b}...")
        self.loading.setVisible(True)
        self.error_row.setVisible(False)
        self.summary.setVisible(False)
        self.columns_host.setVisible(False)

    def compare(self) -> None:
        """Load both seasons and draw the comparison."""
        if self._busy or self.same_selection():
            self._update_state()
            return
        season_a, year_a, season_b, year_b = self.values()
        label_a, label_b = self.labels()
        self._busy = True
        self._token += 1
        token = self._token
        self.compare_btn.setEnabled(False)
        self.set_loading(label_a, label_b)

        def work() -> season_data.CompareResult:
            loader = self._loader
            if loader is None:
                return season_data.compare(
                    season_a, year_a, season_b, year_b, cache_path=self._cache_path
                )
            return loader(season_a, year_a, season_b, year_b)

        def done(result: object) -> None:
            if token != self._token:
                return
            self.set_result(result)  # type: ignore[arg-type]

        def failed(error: object) -> None:
            if token != self._token:
                return
            reason = (season_data.reason_of(error) if isinstance(error, BaseException)
                      else str(error or "the comparison failed"))
            self.set_result(season_data.failed_compare(reason))

        if not self._threaded:
            try:
                done(work())
            except Exception as exc:  # noqa: BLE001 - shown, never raised
                failed(exc)
            return
        run_async(work, done, failed)

    def set_result(self, result: season_data.CompareResult) -> None:
        """Draw ``result`` without loading anything (tests and screenshots)."""
        self._busy = False
        self._result = result
        self.loading.setVisible(False)
        error = str(getattr(result, "error_text", "") or "")
        if error:
            self.error.setText(error)
            self.error_row.setVisible(True)
            self.summary.setVisible(False)
            self.columns_host.setVisible(False)
            self._update_state()
            return
        self.error_row.setVisible(False)
        self._fill_summary(result)
        self.summary.setVisible(True)
        for key in self.COLUMNS:
            self._expanded[key] = False
        for key in self.COLUMNS:
            self._fill_column(key, result)
        self.columns_host.setVisible(True)
        self._update_state()

    def _fill_summary(self, result: season_data.CompareResult) -> None:
        label_a, label_b = self.labels()
        first = str(getattr(result, "first_label", "") or label_a)
        second = str(getattr(result, "second_label", "") or label_b)
        texts = (
            f"{len(result.only_first)} new in {first}",
            f"{len(result.only_second)} only in {second}",
            f"{len(result.shared)} carried over",
        )
        for widget, text in zip(self.count_labels, texts):
            widget.setText(text)

    @staticmethod
    def _entry_meta(entry: Any) -> str:
        bits = []
        score = entry.get("score")
        if score:
            bits.append(f"score {score}")
        fmt = season_data.format_name(entry)
        if fmt:
            bits.append(fmt)
        return " \u00b7 ".join(bits)

    def _fill_column(self, key: str, result: season_data.CompareResult) -> None:
        label_a, label_b = self.labels()
        first = str(getattr(result, "first_label", "") or label_a)
        second = str(getattr(result, "second_label", "") or label_b)
        self._column_heads[key].setText({
            "only_first": f"New in {first}",
            "only_second": f"Only in {second}",
            "shared": "Carried over",
        }[key])
        layout = self._column_rows[key]
        clear_layout(layout)
        entries = list(getattr(result, key) or [])
        limit = len(entries) if self._expanded[key] else self.LIMIT
        for entry in entries[:limit]:
            item = {
                "key": str(entry.get("key") or ""),
                "title": str(entry.get("title") or entry.get("key") or "Untitled"),
                "meta": self._entry_meta(entry),
                "kind": "anime",
                "entry": dict(entry),
            }
            row = FeedRow(item)
            row.clicked.connect(self.item_clicked.emit)
            layout.addWidget(row)
        button = self._column_buttons[key]
        expanded = self._expanded[key]
        # Once a column is open the same button folds it back, so a long list
        # never traps the page at fourteen rows.
        button.setText("Show fewer" if expanded else f"Show all {len(entries)}")
        button.setVisible(len(entries) > self.LIMIT)
        self._column_frames[key].setVisible(bool(entries))

    def toggle_column(self, key: str) -> None:
        """Expand one column in place, or fold it back to five rows."""
        self._expanded[key] = not self._expanded[key]
        if self._result is not None:
            self._fill_column(key, self._result)

    def show_all(self, key: str) -> None:
        """Expand one column in place."""
        if not self._expanded[key]:
            self.toggle_column(key)

    def rows(self, key: str) -> int:
        return self._column_rows[key].count()

    def show_all_button(self, key: str) -> QPushButton:
        return self._column_buttons[key]

    # -- layout -------------------------------------------------------------
    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        narrow = self.width() < self.NARROW
        wanted = QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight
        if self._columns_row.direction() != wanted:
            self._columns_row.setDirection(wanted)


__all__ = ["CompareCard"]
