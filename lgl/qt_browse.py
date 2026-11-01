"""Browse page for the Qt (Windows) frontend.

Layout mirrors the GTK Browse view: a filter column on the left, a page header
with the title, count, sort cluster and grid/list switch, then either wide cover
cards or a compact list.  Selecting a game opens the same "Selected game" panel
the Library uses.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(self, game: GameSummary) -> None:
        super().__init__()
        self.setObjectName("listRow")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.identifier = game.identifier
        self.game = game
        column = QVBoxLayout(self)
        column.setContentsMargins(4, 9, 4, 9)
        column.setSpacing(2)
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
        self.size = "any"
        self.year = "all"
        self.license = "all"
        self.format = "all"
        self._genre_rows: dict[str, CheckRow] = {}

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

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 6)
        genre_head = label("Genre", "filterGroup")
        head.addWidget(genre_head)
        head.addStretch(1)
        self.genre_total = label("All 0", "filterGroup")
        self.genre_total.setCursor(Qt.PointingHandCursor)
        self.genre_total.setToolTip("Clear the genre filter")
        head.addWidget(self.genre_total)
        column.addLayout(head)

        self._genre_host = QVBoxLayout()
        self._genre_host.setSpacing(1)
        column.addLayout(self._genre_host)

        self._group(column, "Size", top=12)
        self._size_group = QButtonGroup(self)
        self._size_group.setExclusive(True)
        column.addWidget(self._pill_flow(SIZE_FILTERS, self._size_group, "any"))
        self._size_group.buttonClicked.connect(self._pick_size)

        self._group(column, "Released", top=14)
        self._year_group = QButtonGroup(self)
        self._year_group.setExclusive(True)
        column.addWidget(self._pill_flow(YEAR_FILTERS, self._year_group, "all"))
        self._year_group.buttonClicked.connect(self._pick_year)

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

    # genres ---------------------------------------------------------------
    def set_genres(self, genres: Sequence[tuple[str, int]], total: int) -> None:
        clear_layout(self._genre_host)
        self._genre_rows.clear()
        self.genre_total.setText(f"All {total}")
        keep = {value.casefold() for value in self.genres}
        self.genres = set()
        for name, count in genres:
            row = CheckRow(name, count, checked=name.casefold() in keep)
            row.toggled.connect(lambda on, n=name: self._toggle_genre(n, on))
            self._genre_host.addWidget(row)
            self._genre_rows[name] = row
            if row.isChecked():
                self.genres.add(name)

    def _toggle_genre(self, name: str, on: bool) -> None:
        if on:
            self.genres.add(name)
        else:
            self.genres.discard(name)
        self.changed.emit()

    def clear_genres(self) -> None:
        if not self.genres:
            return
        self.genres.clear()
        for row in self._genre_rows.values():
            row.setChecked(False)
        self.changed.emit()

    # pickers --------------------------------------------------------------
    def _pick_size(self, button: QPushButton) -> None:
        self.size = str(button.property("key"))
        self.changed.emit()

    def _pick_year(self, button: QPushButton) -> None:
        self.year = str(button.property("key"))
        self.changed.emit()

    def _pick_license(self, button: QPushButton) -> None:
        self.license = str(button.property("key"))
        self.changed.emit()

    def _pick_format(self, button: QPushButton) -> None:
        self.format = str(button.property("key"))
        self.changed.emit()

    def reset(self) -> None:
        self.genres.clear()
        for row in self._genre_rows.values():
            row.setChecked(False)
        for group, attr, value in (
            (self._size_group, "size", "any"),
            (self._year_group, "year", "all"),
            (self._license_group, "license", "all"),
            (self._format_group, "format", "all"),
        ):
            setattr(self, attr, value)
            for button in group.buttons():
                button.setChecked(str(button.property("key")) == value)
        self.changed.emit()


# --------------------------------------------------------------------------
# detail panel
# --------------------------------------------------------------------------


class BrowseDetail(QFrame):
    """Right-hand "Selected game" panel for an archive item."""

    closed = Signal()
    download_requested = Signal(str)
    open_external = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("detail")
        self.setFixedWidth(420)
        self._identifier = ""
        self._item: ArchiveItem | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, _body, column = scroll_page()
        outer.addWidget(scroll)
        column.setContentsMargins(18, 16, 18, 18)
        column.setSpacing(6)

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
        self.download.clicked.connect(lambda: self.download_requested.emit(self._identifier))
        column.addWidget(self.download)
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

    def _open_site(self) -> None:
        if not self._identifier:
            return
        self.open_external.emit(f"https://archive.org/details/{self._identifier}")


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


class BrowsePage(QWidget):
    """Remote catalog browser."""

    download_requested = Signal(str)
    selection_changed = Signal(bool)
    count_changed = Signal(int)

    def __init__(self, client: ArchiveClient | None = None,
                 cache_root: Path | None = None,
                 formats_for: Callable[[str], Sequence[str]] | None = None) -> None:
        super().__init__()
        self.client = client
        self._cache_root = cache_root
        self._formats_for = formats_for or (lambda _identifier: ())
        self._games: list[GameSummary] = []
        self._shown: list[GameSummary] = []
        self._cards: dict[str, QWidget] = {}
        self._pix: dict[str, QPixmap] = {}
        self._selected = ""
        self._descending = False
        self._generation = 0
        self._limit = ROW_LIMIT
        self._view = "grid"
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
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(22)
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
        self._games = list(games)
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
        shown = filter_games(
            self._games,
            query=self._query(),
            year=self.filters.year,
            license_=self.filters.license,
            genres=self.filters.genres,
            size=self.filters.size,
        )
        if self.filters.format != "all":
            shown = [g for g in shown
                     if format_matches(self._formats_for(g.identifier), self.filters.format)]
        self._shown = sort_games(shown, self._sort_key, self._descending)
        self._limit = ROW_LIMIT
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
        available = max(self.scroll.viewport().width() - 48, CARD_MIN)
        cell = max(CARD_MIN, (available - 16 * (columns - 1)) // columns)
        for index, game in enumerate(visible):
            if self._view == "grid":
                card: QWidget = BrowseCard(game)
                card.setMinimumWidth(min(CARD_MIN, cell))
                card.setMaximumWidth(cell)
                self.grid.addWidget(card, index // columns, index % columns)
            else:
                card = BrowseRow(game)
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

    def _columns(self) -> int:
        width = max(self.scroll.viewport().width() - 48, CARD_MIN)
        return max(1, int((width + 16) // (CARD_MIN + 16)))

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
            self.detail.about.setText(
                "Imported catalog records keep their description in the catalog index. "
                "This build does not download from imported catalogs yet."
            )
            self.detail.download.setEnabled(False)
            return
        if self.client is None:
            return
        client = self.client
        identifier = game.identifier

        def done(item: object) -> None:
            if identifier != self._selected or not isinstance(item, ArchiveItem):
                return
            self.detail.show_item(item)
            if not self.detail.cover.has_art():
                self._load_poster(item)

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

    # actions ---------------------------------------------------------------
    def _download(self, identifier: str) -> None:
        self.download_requested.emit(identifier)

    def _open_url(self, url: str) -> None:
        platform_support.open_url(url)


def thumb_url_from_item(item: ArchiveItem) -> str:
    return "https://archive.org/services/img/" + item.identifier


__all__ = ["BrowseCard", "BrowseDetail", "BrowseFilters", "BrowsePage", "BrowseRow"]
