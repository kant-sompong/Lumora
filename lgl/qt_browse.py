"""Browse page for the Qt (Windows) frontend.

Layout mirrors the GTK Browse view: a filter column on the left, a page header
with the title, count, sort cluster and grid/list switch, then either wide cover
cards or a compact list.  Selecting a game opens the same "Selected game" panel
the Library uses.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import browse_filters
from . import platform_support
from .archive import ArchiveClient, ArchiveItem, GameSummary
from .qt_archive import (
    FORMAT_FILTERS,
    LICENSE_FILTERS,
    SIZE_FILTERS,
    SORT_LABELS,
    YEAR_FILTERS,
    bare_title,
    filter_games,
    format_matches,
    genre_counts,
    genre_total,
    license_group,
    sort_games,
    thumb_url,
    tile_meta,
)
from .qt_icons import icon, paint_icon
from .qt_widgets import (
    CheckRow,
    CoverArt,
    CoverGrid,
    DirectionButton,
    ElideLabel,
    EmptyState,
    FlowLayout,
    IconButton,
    SortPill,
    THEME_STATE,
    Toast,
    ViewToggle,
    alpha_color,
    cached_image_bytes,
    clear_layout,
    color,
    divider,
    label,
    pixmap_from_bytes,
    run_async,
    scroll_page,
    search_icon,
    space,
)
from .qt_downloads import FilePickDialog
from .settings import ARCHIVE_SORT_KEYS
from .util import human_size

#: How many results one page of the grid shows before "load more" kicks in.
ROW_LIMIT = 400
CARD_MIN = 250


def _today() -> str:
    return time.strftime("%d %b %Y, %H:%M")


# --------------------------------------------------------------------------
# cards
# --------------------------------------------------------------------------


class BrowseCard(QFrame):
    """Wide (16:9) cover card used by the Browse grid."""

    clicked = Signal(str)

    def __init__(self, game: GameSummary) -> None:
        super().__init__()
        self.setObjectName("coverCard")
        self.setProperty("hover", False)
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.game = game
        self.identifier = game.identifier
        self.setMinimumWidth(CARD_MIN)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        title_text = bare_title(game.title) or game.identifier
        self.cover = CoverArt(radius=6, title=title_text)
        self.cover.setMinimumHeight(120)
        column.addWidget(self.cover)

        self.title = ElideLabel(title_text)
        self.title.setObjectName("coverTitle")
        self.title.setContentsMargins(2, 9, 2, 0)
        column.addWidget(self.title)

        meta = tile_meta(game)
        self.meta = ElideLabel(meta or "—")
        self.meta.setObjectName("coverMeta")
        self.meta.setContentsMargins(2, 1, 2, 0)
        column.addWidget(self.meta)
        self.setFixedHeight(int(CARD_MIN * 0.5625) + 52)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.cover.setFixedHeight(max(90, int(self.width() * 0.5625)))

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self.cover.set_pixmap(pix)

    def set_selected(self, on: bool) -> None:
        self._flag("selected", on)

    def _flag(self, name: str, on: bool) -> None:
        self.setProperty(name, on)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._flag("hover", True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._flag("hover", False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.identifier)
        super().mouseReleaseEvent(event)


class BrowseRow(QFrame):
    """One line in the compact Browse list."""

    clicked = Signal(str)

    def __init__(self, game: GameSummary, *, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("listRow")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.identifier = game.identifier
        self.game = game
        column = QVBoxLayout(self)
        pad = 4 if compact else 9
        column.setContentsMargins(4, pad, 4, pad)
        column.setSpacing(1 if compact else 2)
        title = ElideLabel(bare_title(game.title) or game.identifier)
        title.setObjectName("listTitle")
        column.addWidget(title)
        meta = tile_meta(game) or "—"
        line = ElideLabel(meta)
        line.setObjectName("listMeta")
        column.addWidget(line)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.identifier)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------
# filter column
# --------------------------------------------------------------------------


class BrowseFilters(QFrame):
    """Left column: genre checkboxes, size, released, licence, reset."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("filters")
        self.setFixedWidth(284)
        self.genres: set[str] = set()
        self.exclude_genres: set[str] = set()
        self.size = "any"
        self.min_year = 0
        self.max_year = 0
        self.license = "all"
        self.format = "all"
        self._genre_rows: dict[str, CheckRow] = {}
        self._exclude_buttons: dict[str, QToolButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        column = QVBoxLayout(body)
        column.setContentsMargins(18, 16, 18, 22)
        column.setSpacing(4)

        # The genre block is hidden when no loaded title carries a genre, so a
        # control is never offered that could not match anything.
        self._genre_block = QWidget()
        genre_column = QVBoxLayout(self._genre_block)
        genre_column.setContentsMargins(0, 0, 0, 0)
        genre_column.setSpacing(1)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 6)
        genre_head = label("Genre", "filterGroup")
        head.addWidget(genre_head)
        head.addStretch(1)
        self.genre_total = label("All 0", "filterGroup")
        self.genre_total.setCursor(Qt.PointingHandCursor)
        self.genre_total.setToolTip("Clear the genre filter")
        head.addWidget(self.genre_total)
        genre_column.addLayout(head)

        self._genre_host = QVBoxLayout()
        self._genre_host.setSpacing(1)
        genre_column.addLayout(self._genre_host)
        column.addWidget(self._genre_block)

        self._group(column, "Size", top=12)
        self._size_group = QButtonGroup(self)
        self._size_group.setExclusive(True)
        column.addWidget(self._pill_flow(SIZE_FILTERS, self._size_group, "any"))
        self._size_group.buttonClicked.connect(self._pick_size)

        # Released stays as shortcuts over the year range under it: a decade
        # chip fills the two boxes, so there is one year state and the summary
        # line can never disagree with the controls.
        self._year_block = QWidget()
        year_column = QVBoxLayout(self._year_block)
        year_column.setContentsMargins(0, 0, 0, 0)
        year_column.setSpacing(4)
        self._group(year_column, "Released", top=14)
        self._year_group = QButtonGroup(self)
        self._year_group.setExclusive(True)
        self._year_pills = self._pill_flow(YEAR_FILTERS, self._year_group, "all")
        year_column.addWidget(self._year_pills)
        self._year_group.buttonClicked.connect(self._pick_year)

        years = QHBoxLayout()
        years.setContentsMargins(2, 8, 2, 0)
        years.addWidget(label("Years", "filterGroup"))
        years.addStretch(1)
        year_column.addLayout(years)
        spin_row = QHBoxLayout()
        spin_row.setContentsMargins(2, 0, 2, 0)
        spin_row.setSpacing(4)
        self._from_spin = self._year_spin("From")
        self._to_spin = self._year_spin("To")
        spin_row.addWidget(self._from_spin)
        spin_row.addWidget(label("–"))
        spin_row.addWidget(self._to_spin)
        spin_row.addStretch(1)
        year_column.addLayout(spin_row)
        column.addWidget(self._year_block)

        self._group(column, "Licence", top=14)
        self._license_group = QButtonGroup(self)
        self._license_group.setExclusive(True)
        for key, text in LICENSE_FILTERS:
            btn = self._pill(text, key, self._license_group)
            btn.setChecked(key == "all")
            column.addWidget(btn)
        self._license_group.buttonClicked.connect(self._pick_license)

        self._group(column, "File format", top=14)
        self._format_group = QButtonGroup(self)
        self._format_group.setExclusive(True)
        for key, text in FORMAT_FILTERS:
            btn = self._pill(text, key, self._format_group)
            btn.setChecked(key == "all")
            column.addWidget(btn)
        self._format_group.buttonClicked.connect(self._pick_format)

        self.summary = label("", "filterSummary", wrap=True)
        self.summary.setContentsMargins(2, 14, 2, 0)
        self.summary.hide()
        column.addWidget(self.summary)

        self._group(column, "Availability", top=14)
        reset = QPushButton("Reset all filters")
        reset.setObjectName("resetLink")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self.reset)
        column.addWidget(reset)
        column.addStretch(1)

        self.genre_total.mouseReleaseEvent = lambda _event: self.clear_genres()  # type: ignore[assignment]

    # construction helpers ------------------------------------------------
    def _group(self, column: QVBoxLayout, text: str, *, top: int = 0) -> None:
        column.addSpacing(top)
        head = label(text, "filterGroup")
        head.setContentsMargins(2, 0, 2, 4)
        column.addWidget(head)

    def _pill_flow(self, options: Sequence[tuple[str, str]], group: QButtonGroup,
                   checked: str) -> QWidget:
        """Wrap the small pills so long labels never push the column wider."""
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        flow = FlowLayout(host, spacing=6)
        flow.setContentsMargins(0, 0, 0, 0)
        for key, text in options:
            btn = QPushButton(text)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setChecked(key == checked)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("key", key)
            group.addButton(btn)
            flow.addWidget(btn)
        return host

    def _pill(self, text: str, key: str, group: QButtonGroup) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("radioPill")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("key", key)
        group.addButton(btn)
        return btn

    def _year_spin(self, placeholder: str) -> QSpinBox:
        """A year box where 0 reads as "Any", which is how "no bound" is stored."""
        spin = QSpinBox(self)
        spin.setRange(0, 2200)
        spin.setSpecialValueText("Any")
        spin.setValue(0)
        spin.setFixedWidth(78)
        spin.setToolTip(f"{placeholder} year; Any means no bound")
        spin.setAccessibleName(f"{placeholder} year")
        spin.valueChanged.connect(lambda _value: self._pick_years())
        return spin

    # genres ---------------------------------------------------------------
    def set_genres(self, genres: Sequence[tuple[str, int]], total: int) -> None:
        clear_layout(self._genre_host)
        self._genre_rows.clear()
        self.genre_total.setText(f"All {total}")
        keep = {value.casefold() for value in self.genres}
        excluded = {value.casefold() for value in self.exclude_genres}
        self.genres = set()
        self.exclude_genres = set()
        for name, count in genres:
            row = CheckRow(name, count, checked=name.casefold() in keep)
            row.toggled.connect(lambda on, n=name: self._toggle_genre(n, on))

            never = QToolButton()
            never.setText("\u2715")
            never.setObjectName("genreExclude")
            never.setCheckable(True)
            never.setCursor(Qt.PointingHandCursor)
            never.setToolTip(f"Never show titles tagged {name}")
            never.setAccessibleName(f"Exclude {name}")
            never.setChecked(name.casefold() in excluded)
            never.toggled.connect(lambda on, n=name: self._toggle_exclude(n, on))

            holder = QWidget()
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(2)
            line.addWidget(row, 1)
            line.addWidget(never)
            self._genre_host.addWidget(holder)

            self._genre_rows[name] = row
            self._exclude_buttons[name] = never
            if row.isChecked():
                self.genres.add(name)
            if never.isChecked():
                self.exclude_genres.add(name)
        self.update_summary()

    def _toggle_genre(self, name: str, on: bool) -> None:
        if on:
            self.genres.add(name)
            self.exclude_genres.discard(name)
            self._set_excluded(name, False)
        else:
            self.genres.discard(name)
        self.update_summary()
        self.changed.emit()

    def _toggle_exclude(self, name: str, on: bool) -> None:
        """Never show this genre; it cannot be included at the same time."""
        if on:
            self.exclude_genres.add(name)
            self.genres.discard(name)
            row = self._genre_rows.get(name)
            if row is not None and row.isChecked():
                row.setChecked(False)
        else:
            self.exclude_genres.discard(name)
        self.update_summary()
        self.changed.emit()

    def _set_excluded(self, name: str, value: bool) -> None:
        button = self._exclude_buttons.get(name)
        if button is None or button.isChecked() == value:
            return
        button.blockSignals(True)
        try:
            button.setChecked(value)
        finally:
            button.blockSignals(False)

    def clear_genres(self) -> None:
        if not self.genres and not self.exclude_genres:
            return
        self.genres.clear()
        self.exclude_genres.clear()
        for row in self._genre_rows.values():
            row.setChecked(False)
        for name in self._exclude_buttons:
            self._set_excluded(name, False)
        self.update_summary()
        self.changed.emit()

    # the content filters both frontends share -----------------------------
    def content_filters(self) -> browse_filters.Filters:
        return browse_filters.filters_for(
            min_year=self.min_year or None,
            max_year=self.max_year or None,
            genres=self.genres,
            exclude_genres=self.exclude_genres,
        )

    def update_summary(self) -> None:
        """Show what is being applied, or nothing when nothing is filtered."""
        names = {name.casefold(): name for name in self.genres | self.exclude_genres}
        text = browse_filters.summary(self.content_filters(), display=names)
        self.summary.setText(text)
        self.summary.setVisible(bool(text))

    def set_fields(self, present: set[str]) -> None:
        """Hide a section when no loaded title has the field it filters on."""
        self._genre_block.setVisible(browse_filters.GENRES in present)
        self._year_block.setVisible(browse_filters.YEAR in present)

    # pickers --------------------------------------------------------------
    def _pick_size(self, button: QPushButton) -> None:
        self.size = str(button.property("key"))
        self.changed.emit()

    def _pick_year(self, button: QPushButton) -> None:
        """A century chip is a shortcut: it fills the two year boxes."""
        low, high = browse_filters.bucket_bounds(str(button.property("key")))
        for spin, value in ((self._from_spin, low), (self._to_spin, high)):
            spin.blockSignals(True)
            try:
                spin.setValue(int(value or 0))
            finally:
                spin.blockSignals(False)
        self._pick_years()

    def _pick_years(self) -> None:
        """The two year boxes are the year filter; the chips follow them."""
        self.min_year = self._from_spin.value()
        self.max_year = self._to_spin.value()
        self._sync_year_pills()
        self.update_summary()
        self.changed.emit()

    def _sync_year_pills(self) -> None:
        bucket = browse_filters.bucket_for(
            self.min_year or None, self.max_year or None
        )
        for button in self._year_group.buttons():
            button.setChecked(str(button.property("key")) == bucket)

    def _pick_license(self, button: QPushButton) -> None:
        self.license = str(button.property("key"))
        self.changed.emit()

    def _pick_format(self, button: QPushButton) -> None:
        self.format = str(button.property("key"))
        self.changed.emit()

    def reset(self) -> None:
        self.genres.clear()
        self.exclude_genres.clear()
        for row in self._genre_rows.values():
            row.setChecked(False)
        for name in self._exclude_buttons:
            self._set_excluded(name, False)
        for group, attr, value in (
            (self._size_group, "size", "any"),
            (self._license_group, "license", "all"),
            (self._format_group, "format", "all"),
        ):
            setattr(self, attr, value)
            for button in group.buttons():
                button.setChecked(str(button.property("key")) == value)
        for spin in (self._from_spin, self._to_spin):
            spin.blockSignals(True)
            try:
                spin.setValue(0)
            finally:
                spin.blockSignals(False)
        self.min_year = 0
        self.max_year = 0
        self._sync_year_pills()
        self.update_summary()
        self.changed.emit()


# --------------------------------------------------------------------------
# screenshots and link checks
# --------------------------------------------------------------------------


def probe_urls(urls: Sequence[object], timeout: float = 8.0) -> list[tuple[str, bool, str]]:
    """HEAD each http(s) link and report what actually answered.

    A mirror list is only a list until something is requested: this returns
    ``(url, ok, detail)`` with the status code or the failure, so the UI can say
    "3 of 4 links answered" instead of implying that every link works.
    """
    results: list[tuple[str, bool, str]] = []
    for url in urls:
        text = str(url or "").strip()
        if not text.startswith(("http://", "https://")):
            continue
        request = urllib.request.Request(
            text, method="HEAD", headers={"User-Agent": "Lumora (mirror check)"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                results.append((text, True, str(response.status)))
        except urllib.error.HTTPError as exc:
            results.append((text, False, f"HTTP {exc.code}"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            results.append((text, False, str(exc) or exc.__class__.__name__))
    return results


class ScreenshotViewer(QDialog):
    """Full-size screenshot viewer with previous/next navigation."""

    def __init__(
        self, shots: Sequence[QPixmap], index: int = 0, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle("Screenshots")
        self.resize(1100, 720)
        self._shots = [pix for pix in shots if pix is not None and not pix.isNull()]
        self._index = max(0, min(index, len(self._shots) - 1)) if self._shots else 0

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(320, 200)
        column.addWidget(self.view, 1)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        self.counter = label("", "statLine")
        row.addWidget(self.counter)
        row.addStretch(1)
        for text, delta in (("Previous", -1), ("Next", 1)):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _=False, d=delta: self._step(d))
            row.addWidget(btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        column.addWidget(bar)
        self._render()

    def _step(self, delta: int) -> None:
        if not self._shots:
            return
        self._index = (self._index + delta) % len(self._shots)
        self._render()

    def _render(self) -> None:
        if not self._shots:
            self.counter.setText("No screenshots")
            return
        pix = self._shots[self._index]
        target = QSize(max(self.view.width() - 8, 320), max(self.view.height() - 8, 200))
        self.view.setPixmap(
            pix.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.counter.setText(f"{self._index + 1} of {len(self._shots)}")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render()


# --------------------------------------------------------------------------
# detail panel
# --------------------------------------------------------------------------


class BrowseDetail(QFrame):
    """Right-hand "Selected game" panel for an archive item."""

    closed = Signal()
    download_requested = Signal(str)
    open_external = Signal(str)
    files_requested = Signal(str, object)
    catalog_requested = Signal(str)
    external_requested = Signal(str, str)
    mirror_requested = Signal(str)

    def set_compact(self, compact: bool) -> None:
        """Denser padding in the selected-game panel."""
        self._column.setContentsMargins(
            *((14, 10, 14, 12) if compact else (18, 16, 18, 18))
        )
        self._column.setSpacing(4 if compact else 6)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("detail")
        self.setFixedWidth(420)
        self._identifier = ""
        self._item: ArchiveItem | None = None
        self._is_catalog = False
        self._site_url = ""
        self._shots: list[QPixmap] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, _body, column = scroll_page()
        outer.addWidget(scroll)
        column.setContentsMargins(18, 16, 18, 18)
        column.setSpacing(6)
        self._column = column

        head = QHBoxLayout()
        eyebrow = label("SELECTED GAME", "detailEyebrow")
        head.addWidget(eyebrow)
        head.addStretch(1)
        close = IconButton("close", "Close", 14, 30)
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        column.addLayout(head)

        self.cover = CoverArt(radius=6, crop=True)
        self.cover.setFixedHeight(210)
        column.addSpacing(10)
        column.addWidget(self.cover)

        self.title = label("", "detailTitle", wrap=True)
        self.title.setContentsMargins(0, 12, 0, 0)
        column.addWidget(self.title)
        self.sub = label("", "detailSub", wrap=True)
        column.addWidget(self.sub)
        column.addSpacing(10)

        self._rows: dict[str, QLabel] = {}
        for key, title in (
            ("size", "SIZE"), ("year", "RELEASED"), ("downloads", "DOWNLOADS"),
            ("license", "LICENCE"), ("files", "FILES"),
        ):
            column.addLayout(self._row(key, title))

        column.addSpacing(12)
        column.addWidget(label("ABOUT", "kvKey"))
        self.about = label("", "detailNote", wrap=True)
        column.addWidget(self.about)
        column.addSpacing(12)
        self.shots_head = label("SCREENSHOTS", "kvKey")
        column.addWidget(self.shots_head)
        self.shots_host = QWidget()
        self.shots_row = QHBoxLayout(self.shots_host)
        self.shots_row.setContentsMargins(0, 0, 0, 0)
        self.shots_row.setSpacing(6)
        column.addWidget(self.shots_host)
        self.shots_head.hide()
        self.shots_host.hide()

        self.files_head = label("FILES", "kvKey")
        column.addWidget(self.files_head)
        self.files_note = label("", "detailNote", wrap=True)
        column.addWidget(self.files_note)
        column.addStretch(1)
        column.addSpacing(12)

        self.download = QPushButton("Download")
        self.download.setObjectName("suggested")
        self.download.setMinimumHeight(38)
        self.download.setCursor(Qt.PointingHandCursor)
        self.download.clicked.connect(self._download_clicked)
        column.addWidget(self.download)

        self.files_btn = QPushButton("Download selected…")
        self.files_btn.setObjectName("quiet")
        self.files_btn.setMinimumHeight(34)
        self.files_btn.setCursor(Qt.PointingHandCursor)
        self.files_btn.setToolTip("Choose which files of this item to download")
        self.files_btn.clicked.connect(self._pick_files)
        self.files_btn.hide()
        column.addWidget(self.files_btn)

        extras = QHBoxLayout()
        extras.setSpacing(8)
        self.copy_btn = QPushButton("Copy identifier")
        self.copy_btn.setObjectName("quiet")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_identifier)
        extras.addWidget(self.copy_btn)
        self.mirror_btn = QPushButton("Check mirrors")
        self.mirror_btn.setObjectName("quiet")
        self.mirror_btn.setCursor(Qt.PointingHandCursor)
        self.mirror_btn.setToolTip("Send a HEAD request to each download link and report what answered")
        self.mirror_btn.clicked.connect(lambda: self.mirror_requested.emit(self._identifier))
        self.mirror_btn.hide()
        extras.addWidget(self.mirror_btn)
        column.addLayout(extras)

        managers = QHBoxLayout()
        managers.setSpacing(8)
        for name, text in (
            ("jdownloader", "JDownloader"),
            ("ab_download_manager", "AB Download Manager"),
        ):
            btn = QPushButton(text)
            btn.setObjectName("quiet")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Send this item's links to {text} on this computer")
            btn.clicked.connect(lambda _=False, key=name: self.external_requested.emit(key, self._identifier))
            btn.hide()
            managers.addWidget(btn)
            if name == "jdownloader":
                self.jd_btn = btn
            else:
                self.ab_btn = btn
        column.addLayout(managers)

        self.open_btn = QPushButton("Open on archive.org")
        self.open_btn.setObjectName("quiet")
        self.open_btn.setMinimumHeight(34)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_site)
        column.addWidget(self.open_btn)

    def _row(self, key: str, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        head = label(title, "kvKey")
        head.setFixedWidth(92)
        row.addWidget(head, 0, Qt.AlignTop)
        value = label("—", "kvVal", wrap=True)
        row.addWidget(value, 1)
        self._rows[key] = value
        return row

    def _put(self, key: str, text: str, kind: str = "kvVal") -> None:
        value = self._rows[key]
        value.setText(text or "—")
        value.setObjectName(kind)
        value.style().unpolish(value)
        value.style().polish(value)

    def show_game(self, game: GameSummary, pix: QPixmap | None = None) -> None:
        self._identifier = game.identifier
        self._item = None
        self._is_catalog = bool(getattr(game, "is_catalog", False))
        self._site_url = str(getattr(game, "source_url", "") or "")
        self.clear_screenshots()
        self.files_btn.hide()
        self.mirror_btn.hide()
        self.jd_btn.hide()
        self.ab_btn.hide()
        self.open_btn.setText("Open source page")
        self.download.setText(
            "Download from catalogue…" if self._is_catalog else "Download"
        )
        title_text = bare_title(game.title) or game.identifier
        self.title.setText(title_text)
        self.cover.set_title(title_text)
        self.sub.setText(
            " · ".join(b for b in (game.creator, str(game.year or "")) if b)
            or "No creator or year recorded"
        )
        self._put("size", human_size(game.item_size) if game.item_size else "")
        self._put("year", str(game.year or ""))
        self._put("downloads", f"{game.downloads:,}" if game.downloads else "")
        self._put("license", dict(LICENSE_FILTERS)[license_group(game.licenseurl)])
        self._put("files", "")
        self.about.setText("Loading the item details…")
        self.files_head.setVisible(not game.is_catalog)
        self.files_note.setVisible(not game.is_catalog)
        self.files_note.setText("")
        self.download.setEnabled(True)
        if pix is not None:
            self.cover.set_pixmap(pix)

    def show_item(self, item: ArchiveItem) -> None:
        self._item = item
        self.about.setText(item.description or "This item has no description.")
        self.files_btn.setVisible(bool(item.files))
        self.mirror_btn.setVisible(True)
        self.jd_btn.setVisible(True)
        self.ab_btn.setVisible(True)
        self.open_btn.setText("Open on archive.org")
        files = item.primary_files
        self._put("files", f"{len(item.files)} files")
        if files:
            biggest = max(files, key=lambda f: f.size or 0)
            self.files_note.setText(
                f"{len(files)} game files · {human_size(sum(f.size or 0 for f in files))} "
                f"in total · largest: {biggest.name}"
            )
        else:
            self.files_note.setText("No downloadable game files were listed.")

    # actions ---------------------------------------------------------------
    def _download_clicked(self) -> None:
        if not self._identifier:
            return
        if self._is_catalog:
            self.catalog_requested.emit(self._identifier)
        else:
            self.download_requested.emit(self._identifier)

    def _pick_files(self) -> None:
        if self._item is None:
            return
        files = [f for f in self._item.files if f.downloadable]
        if not files:
            return
        dialog = FilePickDialog(self._item.title or self._identifier, files, self)
        if dialog.exec() == QDialog.Accepted:
            chosen = dialog.chosen()
            if chosen:
                self.files_requested.emit(self._identifier, chosen)

    def _copy_identifier(self) -> None:
        if not self._identifier:
            return
        QApplication.clipboard().setText(self._identifier)
        self.copy_btn.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copy identifier"))

    def clear_screenshots(self) -> None:
        clear_layout(self.shots_row)
        self._shots.clear()
        self.shots_head.hide()
        self.shots_host.hide()

    def add_screenshot(self, pix: QPixmap | None) -> None:
        if pix is None or pix.isNull():
            return
        self._shots.append(pix)
        index = len(self._shots) - 1
        thumb = QToolButton()
        thumb.setObjectName("shotThumb")
        thumb.setCursor(Qt.PointingHandCursor)
        thumb.setToolTip("Open this screenshot")
        thumb.setAccessibleName("Open this screenshot")
        thumb.setIcon(QIcon(pix.scaled(150, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        thumb.setIconSize(QSize(150, 84))
        thumb.setFixedSize(150, 84)
        thumb.clicked.connect(lambda _=False, i=index: self._open_viewer(i))
        self.shots_row.addWidget(thumb)
        self.shots_head.show()
        self.shots_host.show()

    def _open_viewer(self, index: int) -> None:
        ScreenshotViewer(self._shots, index, self).exec()

    def _open_site(self) -> None:
        if not self._identifier:
            return
        url = self._site_url or f"https://archive.org/details/{self._identifier}"
        self.open_external.emit(url)


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


class BrowsePage(QWidget):
    """Remote catalog browser."""

    download_requested = Signal(str)
    files_download_requested = Signal(object)
    selection_changed = Signal(bool)
    count_changed = Signal(int)
    catalog_download_requested = Signal(str)

    def __init__(self, client: ArchiveClient | None = None,
                 cache_root: Path | None = None,
                 formats_for: Callable[[str], Sequence[str]] | None = None,
                 catalog: object | None = None,
                 settings: object | None = None) -> None:
        super().__init__()
        self.client = client
        self.catalog = catalog
        self.settings = settings
        self._cache_root = cache_root
        self._formats_for = formats_for or (lambda _identifier: ())
        self._games: list[GameSummary] = []
        #: The archive collection and the imported catalogue are kept apart:
        #: either can be replaced without losing the other.
        self._archive_games: list[GameSummary] = []
        self._catalog_entries: list[tuple[GameSummary, str]] = []
        self._shown: list[GameSummary] = []
        self._cards: dict[str, QWidget] = {}
        self._pix: dict[str, QPixmap] = {}
        self._items: dict[str, ArchiveItem] = {}
        self._selected = ""
        self._descending = False
        self._generation = 0
        self._limit = ROW_LIMIT
        self._view = "grid"
        self._compact = False
        self._loading = False
        self._warning = ""
        self._stamp = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.filters = BrowseFilters()
        self.filters.changed.connect(self.refresh)
        root.addWidget(self.filters)

        body = QWidget()
        body.setObjectName("pageBody")
        root.addWidget(body, 1)
        page = QVBoxLayout(body)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        header = QWidget()
        head = QHBoxLayout(header)
        head.setContentsMargins(24, 18, 24, 14)
        head.setSpacing(10)
        self._head = head
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(label("Browse", "pageTitle"), 0, Qt.AlignBottom)
        self.count = label("Loading…", "pageCount")
        self.count.setContentsMargins(0, 0, 0, 4)
        heading.addWidget(self.count, 0, Qt.AlignBottom)
        head.addLayout(heading)
        head.addStretch(1)

        self.sort = SortPill("Sort: Title", "Sort the archive list")
        for key in ARCHIVE_SORT_KEYS:
            self.sort.setProperty(key, SORT_LABELS[key])
        self._sort_key = "title"
        self.sort.clicked.connect(lambda: self._sort_menu())
        self.sort.setMinimumWidth(150)
        head.addWidget(self.sort)
        self.direction = DirectionButton()
        self.direction.clicked.connect(self._flip)
        head.addWidget(self.direction)
        self.view_toggle = ViewToggle("Show as a list")
        self.view_toggle.clicked.connect(self._toggle_view)
        self.view_toggle.set_list(False)
        head.addWidget(self.view_toggle)
        self.refresh_btn = IconButton("refresh", "Reload the catalog", 17, 34)
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        head.addWidget(self.refresh_btn)
        page.addWidget(header)

        self.scroll, self._scroll_body, column = scroll_page()
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_more)
        page.addWidget(self.scroll, 1)
        column.setContentsMargins(24, 0, 24, 8)
        column.setSpacing(0)
        self._column = column
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(22)
        self.shelves_host = QWidget()
        self.shelves_column = QVBoxLayout(self.shelves_host)
        self.shelves_column.setContentsMargins(0, 4, 0, 10)
        self.shelves_column.setSpacing(6)
        self.shelves_host.hide()
        column.addWidget(self.shelves_host)
        column.addWidget(self.grid_host)
        self.list_host = QWidget()
        self.list_column = QVBoxLayout(self.list_host)
        self.list_column.setContentsMargins(0, 0, 0, 0)
        self.list_column.setSpacing(0)
        self.list_host.hide()
        column.addWidget(self.list_host)
        self.empty = EmptyState("search", "Loading the catalog…",
                                "The collection index is downloaded once and cached for six hours.")
        column.addWidget(self.empty)
        column.addStretch(1)

        footer = QWidget()
        foot = QVBoxLayout(footer)
        foot.setContentsMargins(24, 8, 24, 6)
        foot.setSpacing(2)
        self.footer_note = label("", "statLine")
        foot.addWidget(self.footer_note)
        page.addWidget(footer)

        status = QFrame()
        status.setObjectName("metabar")
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(24, 4, 24, 4)
        self.status_bar = label("", "statusBar")
        status_row.addWidget(self.status_bar)
        page.addWidget(status)

        self.detail = BrowseDetail()
        self.detail.hide()
        self.detail.closed.connect(self.clear_selection)
        self.detail.download_requested.connect(self._download)
        self.detail.open_external.connect(self._open_url)
        self.detail.files_requested.connect(self._download_files)
        self.detail.catalog_requested.connect(self.catalog_download_requested)
        self.detail.external_requested.connect(self._send_external)
        self.detail.mirror_requested.connect(self._check_mirrors)
        root.addWidget(self.detail)

    # data ------------------------------------------------------------------
    def load(self, force: bool = False) -> None:
        if self.client is None or self._loading:
            return
        self._loading = True
        self.refresh_btn.setEnabled(False)
        if not self._games:
            self.count.setText("Loading…")
        client = self.client

        def done(result: object) -> None:
            self._loading = False
            self.refresh_btn.setEnabled(True)
            self.set_games(getattr(result, "games", []))
            self._warning = getattr(result, "warning", "") or ""
            self._stamp = time.strftime("%d %b %Y, %H:%M")
            self._update_footer()

        def failed(exc: object) -> None:
            self._loading = False
            self.refresh_btn.setEnabled(True)
            self._warning = str(exc) or "Could not load the catalog."
            self.count.setText("Unavailable")
            self.empty.set_text("The catalog could not be loaded", self._warning)
            self.empty.show()
            self._update_footer()

        run_async(lambda: client.fetch_collection(force=force), done, failed)

    def set_games(self, games: Sequence[GameSummary]) -> None:
        """Replace the archive list; imported catalogue records stay listed."""
        self._archive_games = list(games)
        self._merge_catalog()

    def reload_catalog(self, announce: bool = False) -> int:
        """Re-read imported catalogue records and merge them into the list.

        This is what the GTK build does after a website or Kryoto import, and
        without it an import would be stored and still show nothing: Browse
        only ever listed the archive collection.
        """
        self._catalog_entries = self._catalog_summaries()
        self._merge_catalog()
        count = len(self._catalog_entries)
        if announce and count:
            self._update_footer()
        return count

    def _catalog_summaries(self) -> list[tuple[GameSummary, str]]:
        """One Browse row per imported record, plus its source game id."""
        if self.catalog is None:
            return []
        try:
            records = list(self.catalog.iter_games())
        except Exception:  # noqa: BLE001 - a broken store must not break Browse
            return []
        entries: list[tuple[GameSummary, str]] = []
        for record in records:
            key = record.key
            try:
                source = self.catalog.get_source(record.source_id)
            except Exception:  # noqa: BLE001
                source = None
            try:
                size = self.catalog.estimated_size(key)
            except Exception:  # noqa: BLE001
                size = None
            try:
                state = self.catalog.download_state(key)
            except Exception:  # noqa: BLE001
                state = ""
            entries.append((
                GameSummary(
                    identifier=f"catalog:{key}",
                    title=record.title or record.game_id,
                    year=record.year,
                    downloads=0,
                    item_size=size,
                    creator=record.creator or record.developer or record.publisher,
                    subjects=list(record.genres),
                    catalog_key=key,
                    catalog_source=(source.name if source is not None else "") or record.source_id,
                    source_url=record.source_url,
                    cover_url=record.cover_url,
                    download_state=state,
                ),
                record.game_id or "",
            ))
        return entries

    def _merge_catalog(self) -> None:
        """Append catalogue records the archive list does not already hold."""
        base = [game for game in self._archive_games if not game.is_catalog]
        known = {game.identifier for game in base}
        merged = list(base)
        for game, game_id in self._catalog_entries:
            if game_id and game_id in known:
                continue
            merged.append(game)
        self._games = merged
        self.filters.set_genres(genre_counts(self._games), genre_total(self._games))
        self.refresh()

    def game(self, identifier: str) -> GameSummary | None:
        return next((g for g in self._games if g.identifier == identifier), None)

    # sorting / view --------------------------------------------------------
    def _sort_menu(self) -> None:
        from .qt_widgets import Popover

        popover = Popover(self)
        for key in ARCHIVE_SORT_KEYS:
            btn = QPushButton(SORT_LABELS[key])
            btn.setObjectName("popoverRow")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._set_sort(k))
            popover.add_widget(btn)
        popover.show_at(self.sort)
        self._sort_popover = popover

    def _set_sort(self, key: str) -> None:
        self._sort_key = key
        self.sort.setText(f"Sort: {SORT_LABELS.get(key, key)}")
        popover = getattr(self, "_sort_popover", None)
        if popover is not None:
            popover.hide()
        self.refresh()

    def _flip(self) -> None:
        self._descending = not self._descending
        self.direction.set_down(self._descending)
        self.refresh()

    def _toggle_view(self) -> None:
        self._view = "list" if self._view == "grid" else "grid"
        self.view_toggle.set_list(self._view == "list")
        self._render()

    # filtering -------------------------------------------------------------
    def refresh(self) -> None:
        self.filters.set_fields(browse_filters.available(self._games))
        self.filters.update_summary()
        shown = filter_games(
            self._games,
            query=self._query(),
            license_=self.filters.license,
            genres=self.filters.genres,
            exclude_genres=self.filters.exclude_genres,
            min_year=self.filters.min_year or None,
            max_year=self.filters.max_year or None,
            size=self.filters.size,
        )
        if self.filters.format != "all":
            shown = [g for g in shown
                     if format_matches(self._formats_for(g.identifier), self.filters.format)]
        self._shown = sort_games(shown, self._sort_key, self._descending)
        self._limit = ROW_LIMIT
        self._render_shelves()
        if self._view == "grid":
            self.list_host.hide()
            self.grid_host.show()
        else:
            self.grid_host.hide()
            self.list_host.show()
        self._render()
        self._update_footer()

    def _query(self) -> str:
        window = self.window()
        field = getattr(window, "search_field", None)
        return field.text() if field is not None else ""

    def _update_footer(self) -> None:
        total = len(self._games)
        if not total and not self._warning:
            self.count.setText("")
            self.footer_note.setText("")
            self.count_changed.emit(0)
            return
        self.count.setText(f"{total:,} games" if total else "Unavailable")
        self.count_changed.emit(total)
        shown = min(len(self._shown), self._limit)
        if self._warning and not total:
            self.footer_note.setText(self._warning)
        elif len(self._shown) > shown:
            self.footer_note.setText(
                f"Showing {shown} of {len(self._shown):,} matches — scroll for more "
                "or refine the search."
            )
        else:
            self.footer_note.setText(
                f"Showing {shown:,} of {len(self._shown):,} matches."
                if self._shown else "No games match these filters."
            )
        if self.filters.format != "all":
            self.footer_note.setText(
                self.footer_note.text()
                + "  File formats are matched against metadata that is already cached."
            )
        self.status_bar.setText(
            f"Saved list · {self._stamp}" if self._stamp else ""
        )

    # rendering -------------------------------------------------------------
    def set_compact(self, compact: bool) -> None:
        """Shrink page padding, list rows and tile spacing; off restores it."""
        self._compact = bool(compact)
        self._head.setContentsMargins(
            *((16, 12, 16, 10) if compact else (24, 18, 24, 14))
        )
        self._column.setContentsMargins(
            *((16, 0, 16, 4) if compact else (24, 0, 24, 8))
        )
        self.grid.setHorizontalSpacing(10 if compact else 16)
        self.grid.setVerticalSpacing(14 if compact else 22)
        self.detail.set_compact(compact)
        if self._view == "list":
            # Rows are built at a fixed padding, so rebuild the ones on screen.
            self._render()

    def _render(self) -> None:
        self._generation += 1
        self.grid_host.setVisible(self._view == "grid")
        self.list_host.setVisible(self._view == "list")
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        clear_layout(self.list_column)
        self._cards.clear()

        limit = min(len(self._shown), self._limit)
        visible = self._shown[:limit]
        columns = self._columns()
        base = self._card_min()
        available = max(self.scroll.viewport().width() - 48, base)
        cell = max(base, (available - 16 * (columns - 1)) // columns)
        for index, game in enumerate(visible):
            if self._view == "grid":
                card: QWidget = BrowseCard(game)
                card.setMinimumWidth(min(CARD_MIN, cell))
                card.setMaximumWidth(cell)
                self.grid.addWidget(card, index // columns, index % columns)
            else:
                card = BrowseRow(game, compact=self._compact)
                self.list_column.addWidget(card)
            card.clicked.connect(self.select)  # type: ignore[attr-defined]
            if game.identifier in self._pix:
                card.set_pixmap(self._pix[game.identifier])  # type: ignore[attr-defined]
            self._cards[game.identifier] = card

        has = bool(visible)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            if self._games:
                self.empty.set_text("No games match these filters",
                                    "Adjust the filters on the left, or reset them.")
            elif not self._warning:
                self.empty.set_text("Loading the catalog…",
                                    "The collection index is downloaded once and cached for six hours.")
        if self._selected in self._cards:
            self._cards[self._selected].set_selected(True)  # type: ignore[attr-defined]
        self._load_covers()

    def _card_min(self) -> int:
        """Base card width for the chosen home layout."""
        from . import home_layout

        if self.settings is None:
            return CARD_MIN
        try:
            return max(90, int(CARD_MIN * home_layout.scale(home_layout.HomeLayout(self.settings).layout())))
        except Exception:  # noqa: BLE001 - a broken settings file must not break Browse
            return CARD_MIN

    def _render_shelves(self) -> None:
        """Custom shelves above the grid, one row of titles each."""
        from . import shelves as shelves_lib

        if self.shelves_column.count():
            clear_layout(self.shelves_column)
        if self.settings is None or not self._games:
            self.shelves_host.hide()
            return
        rows = [
            {
                "key": game.identifier,
                "title": game.title,
                "year": game.year,
                "genres": list(game.subjects),
                "rating": None,
            }
            for game in self._games
        ]
        rendered = shelves_lib.render(self.settings, rows)
        for name, items in rendered:
            self.shelves_column.addWidget(label(name.upper(), "detailEyebrow"))
            line = QWidget()
            flow = QHBoxLayout(line)
            flow.setContentsMargins(0, 0, 0, 0)
            flow.setSpacing(8)
            for item in items:
                key = str(item.get("key") or "")
                pill = QPushButton(str(item.get("title") or key))
                pill.setObjectName("shelfPill")
                pill.setCursor(Qt.PointingHandCursor)
                pill.clicked.connect(lambda _=False, k=key: self._open_shelf_item(k))
                flow.addWidget(pill)
            flow.addStretch(1)
            self.shelves_column.addWidget(line)
        self.shelves_host.setVisible(bool(rendered))

    def _open_shelf_item(self, key: str) -> None:
        if key and self.game(key) is not None:
            self.select(key)

    def _columns(self) -> int:
        base = self._card_min()
        width = max(self.scroll.viewport().width() - 48, base)
        return max(1, int((width + 16) // (base + 16)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = self.scroll.viewport().width()
        if abs(width - getattr(self, "_last_width", 0)) > 8:
            self._last_width = width
            self._last_columns = self._columns()
            if self._cards:
                QTimer.singleShot(0, self._render)

    def _maybe_more(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if value >= bar.maximum() - 40 and len(self._shown) > self._limit:
            self._limit += ROW_LIMIT
            self._render()
            self._update_footer()
            return
        self._load_covers()

    # covers ----------------------------------------------------------------
    def _load_covers(self) -> None:
        if self.client is None:
            return
        top = self.scroll.verticalScrollBar().value() - 500
        bottom = top + self.scroll.viewport().height() + 1200
        generation = self._generation
        for game in self._shown[: self._limit]:
            card = self._cards.get(game.identifier)
            if card is None or getattr(card, "_cover_asked", False):
                continue
            if not thumb_url(game):
                continue
            if not (top <= card.y() <= bottom):
                continue
            card._cover_asked = True  # type: ignore[attr-defined]
            url = thumb_url(game)
            client = self.client

            def fetch(u: str = url, g: GameSummary = game) -> bytes:
                if g.is_catalog:
                    return cached_image_bytes(u, platform_support.cache_dir(),
                                              f"catalog-{g.identifier}")
                return client.fetch_image(u, f"thumb-{g.identifier}")

            run_async(
                fetch,
                lambda data, i=game.identifier, gen=generation: self._cover_ready(i, gen, data),
            )

    def _cover_ready(self, identifier: str, generation: int, data: object) -> None:
        pix = pixmap_from_bytes(data)
        if pix is None:
            return
        self._pix[identifier] = pix
        if generation != self._generation:
            return
        card = self._cards.get(identifier)
        if card is not None and hasattr(card, "set_pixmap"):
            card.set_pixmap(pix)  # type: ignore[attr-defined]
            card.update()
        if identifier == self._selected:
            game = self.game(identifier)
            if game is not None:
                self.detail.cover.set_pixmap(pix)

    # selection -------------------------------------------------------------
    def select(self, identifier: str) -> None:
        previous = self._cards.get(self._selected)
        if previous is not None:
            previous.set_selected(False)  # type: ignore[attr-defined]
        self._selected = identifier
        card = self._cards.get(identifier)
        if card is not None:
            card.set_selected(True)  # type: ignore[attr-defined]
        game = self.game(identifier)
        if game is None:
            return
        self.detail.show_game(game, self._pix.get(identifier))
        self.detail.show()
        self.selection_changed.emit(True)
        self._load_item(game)

    def _load_item(self, game: GameSummary) -> None:
        if game.is_catalog:
            self.detail.download.setEnabled(True)
            self.detail.about.setText(
                "Imported catalogue record. Choose “Download from catalogue…” to "
                "queue its verified links, or the ☰ menu → “Download from "
                "catalogue…” for every record."
            )
            return
        if self.client is None:
            return
        client = self.client
        identifier = game.identifier

        def done(item: object) -> None:
            if identifier != self._selected or not isinstance(item, ArchiveItem):
                return
            self._items[identifier] = item
            self.detail.show_item(item)
            if not self.detail.cover.has_art():
                self._load_poster(item)
            self._load_screenshots(item)

        def failed(exc: object) -> None:
            if identifier == self._selected:
                self.detail.about.setText(f"Details could not be loaded: {exc}")

        run_async(lambda: client.fetch_item(identifier), done, failed)

    def _load_poster(self, item: ArchiveItem) -> None:
        url = item.poster_url if item.poster_url else thumb_url_from_item(item)
        if not url or self.client is None:
            return

        def done(data: object) -> None:
            pix = pixmap_from_bytes(data)
            if pix is not None and item.identifier == self._selected:
                self._pix[item.identifier] = pix
                self.detail.cover.set_pixmap(pix)

        run_async(lambda: self.client.fetch_image(url, f"poster-{item.identifier}"), done)

    def clear_selection(self) -> None:
        card = self._cards.get(self._selected)
        if card is not None:
            card.set_selected(False)  # type: ignore[attr-defined]
        self._selected = ""
        self.detail.hide()
        self.selection_changed.emit(False)

    def _load_screenshots(self, item: ArchiveItem) -> None:
        """Fetch the item's gameplay screenshots for the thumbnail strip."""
        self.detail.clear_screenshots()
        files = list(item.screenshot_files)[:6]
        if not files or self.client is None:
            return
        client = self.client
        identifier = item.identifier

        def done_all(pixmaps: object) -> None:
            if item.identifier != self._selected:
                return
            for pix in pixmaps or ():  # type: ignore[union-attr]
                self.detail.add_screenshot(pix)

        def fetch(url: str, key: str) -> object:
            return pixmap_from_bytes(client.fetch_image(url, key))

        def work() -> list[object]:
            shots: list[object] = []
            for archive_file in files:
                try:
                    pix = fetch(
                        item.file_url(archive_file),
                        f"shot-{identifier}-{Path(archive_file.name).name}",
                    )
                except (OSError, ValueError):
                    continue
                if pix is not None:
                    shots.append(pix)
            return shots

        run_async(work, done_all)

    # actions ---------------------------------------------------------------
    def _download(self, identifier: str) -> None:
        self.download_requested.emit(identifier)

    def _download_files(self, identifier: str, files: object) -> None:
        item = self._items.get(identifier)
        chosen = list(files) if files else []  # type: ignore[arg-type]
        if item is None or not chosen:
            return
        self.files_download_requested.emit((item, chosen))

    def _send_external(self, name: str, identifier: str) -> None:
        """Hand this item's links to a locally running download manager."""
        item = self._items.get(identifier)
        toast = getattr(self.window(), "toast", None)
        if item is None:
            if isinstance(toast, Toast):
                toast.show_message("Load the item details first.", error=True)
            return
        urls = [item.file_url(f) for f in item.files if f.downloadable]
        if not urls:
            if isinstance(toast, Toast):
                toast.show_message("This item has no downloadable links.", error=True)
            return
        from .qt_downloads import send_to_external

        folder = str(getattr(self.window(), "download_dir", ""))

        def done(count: object) -> None:
            if isinstance(toast, Toast):
                toast.show_message(f"Sent {count} link(s) to the download manager.")

        def failed(exc: object) -> None:
            if isinstance(toast, Toast):
                toast.show_message(f"Could not send the links: {exc}", error=True)

        run_async(lambda: send_to_external(name, urls, folder), done, failed)

    def _check_mirrors(self, identifier: str) -> None:
        item = self._items.get(identifier)
        toast = getattr(self.window(), "toast", None)
        if item is None:
            return
        urls = [item.file_url(f) for f in item.files if f.downloadable][:8]
        if not urls:
            if isinstance(toast, Toast):
                toast.show_message("This item has no links to check.", error=True)
            return

        def done(result: object) -> None:
            if not isinstance(toast, Toast):
                return
            rows = list(result) if isinstance(result, list) else []
            ok = sum(1 for _url, good, _detail in rows if good)
            detail = " · ".join(
                f"{url.rsplit('/', 1)[-1] or url}: {'answered' if good else why}"
                for url, good, why in rows[:3]
            )
            toast.show_message(
                f"{ok} of {len(rows)} links answered. {detail}".strip(),
                error=ok == 0,
            )

        run_async(lambda: probe_urls(urls), done,
                  lambda exc: toast.show_message(f"Mirror check failed: {exc}", error=True)
                  if isinstance(toast, Toast) else None)

    def _open_url(self, url: str) -> None:
        platform_support.open_url(url)


def thumb_url_from_item(item: ArchiveItem) -> str:
    return "https://archive.org/services/img/" + item.identifier


__all__ = [
    "BrowseCard",
    "BrowseDetail",
    "BrowseFilters",
    "BrowsePage",
    "BrowseRow",
    "ScreenshotViewer",
    "probe_urls",
]
