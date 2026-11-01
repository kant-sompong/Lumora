"""Archive view for the Qt frontend: the remote catalog of games.

Layout follows the GTK Browse view: a filter column on the left, a toolbar
(search, sort + direction), a poster grid, and a "Selected game" panel on the
right.  The filtering and sorting live in plain functions at the top of this
module so they can be unit-tested without a display.
"""

from __future__ import annotations

import urllib.parse
from typing import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .archive import ArchiveClient, GameSummary
from .catalog_store import DOWNLOAD_STATE_LABELS
from .qt_widgets import THEME_STATE, CoverArt, FlowLayout, clear_layout, run_async, search_icon
from .settings import ARCHIVE_SORT_KEYS
from .util import human_size

COVER_W, COVER_H = 150, 225

SORT_LABELS = {
    "title": "Title",
    "year": "Year",
    "size": "Size",
    "downloads": "Downloads",
    "downloadable": "Download",
    "creator": "Creator",
    "genre": "Genre",
}

YEAR_FILTERS = (
    ("all", "All years"),
    ("pre1990", "Before 1990"),
    ("1990s", "1990s"),
    ("2000s", "2000s"),
    ("2010s", "2010s"),
    ("2020s", "2020s and later"),
)
LICENSE_FILTERS = (
    ("all", "All licenses"),
    ("publicdomain", "Public domain"),
    ("cc", "Creative Commons"),
    ("other", "Other license"),
    ("none", "No license listed"),
)


# --------------------------------------------------------------------------
# pure logic (no Qt) - mirrors ui.py so both frontends agree
# --------------------------------------------------------------------------


def license_group(url: str) -> str:
    text = (url or "").lower()
    if not text:
        return "none"
    if "publicdomain" in text or "public-domain" in text or "mark" in text:
        return "publicdomain"
    if "creativecommons" in text or "/licenses/" in text:
        return "cc"
    return "other"


def year_matches(year: str, bucket: str) -> bool:
    if bucket == "all":
        return True
    try:
        value = int(str(year)[:4])
    except (TypeError, ValueError):
        return False
    return {
        "pre1990": value < 1990,
        "1990s": 1990 <= value < 2000,
        "2000s": 2000 <= value < 2010,
        "2010s": 2010 <= value < 2020,
        "2020s": value >= 2020,
    }.get(bucket, True)


def bare_title(title: object) -> str:
    """``Doom (v1.9) [Linux]`` -> ``Doom``."""
    text = " ".join(str(title or "").split())
    for marker in (" (", " [", " {"):
        head, sep, _tail = text.partition(marker)
        if sep and head.strip():
            text = head.strip()
    return text


def tile_meta(game: GameSummary) -> str:
    bits: list[str] = []
    if game.item_size:
        bits.append(human_size(game.item_size))
    if game.year:
        bits.append(str(game.year))
    if game.is_catalog:
        state = DOWNLOAD_STATE_LABELS.get(game.download_state)
        if state:
            bits.append(state)
    return " · ".join(bits)


def thumb_url(game: GameSummary) -> str:
    if game.cover_url:
        return game.cover_url
    if not game.identifier or game.identifier.startswith("catalog:"):
        return ""
    return "https://archive.org/services/img/" + urllib.parse.quote(game.identifier, safe="")


def filter_games(
    games: Sequence[GameSummary],
    *,
    query: str = "",
    year: str = "all",
    license_: str = "all",
    genre: str = "all",
) -> list[GameSummary]:
    out = ArchiveClient.search(list(games), query) if query.strip() else list(games)
    if year != "all":
        out = [g for g in out if year_matches(g.year, year)]
    if license_ != "all":
        out = [g for g in out if license_group(g.licenseurl) == license_]
    if genre != "all":
        out = [g for g in out if genre.casefold() in {s.casefold() for s in g.subjects}]
    return out


def _num(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _year_num(game: GameSummary) -> int:
    try:
        return int(str(game.year)[:4])
    except (TypeError, ValueError):
        return 0


def sort_games(games: Sequence[GameSummary], key: str, descending: bool) -> list[GameSummary]:
    """Sort with missing values last in both directions; ties fall back to title."""
    if key not in ARCHIVE_SORT_KEYS:
        key = "title"

    def value(game: GameSummary) -> object:
        if key == "year":
            return _year_num(game) or None
        if key == "size":
            return _num(game.item_size) or None
        if key == "downloads":
            return _num(game.downloads) or None
        if key == "downloadable":
            return None if game.is_catalog and game.download_state in ("none", "broken") else 1
        if key == "creator":
            return (game.creator or "").casefold() or None
        if key == "genre":
            return (game.subjects[0].casefold() if game.subjects else "") or None
        return (game.title or "").casefold()

    present = [g for g in games if value(g) is not None]
    missing = [g for g in games if value(g) is None]
    present.sort(key=lambda g: (g.title or "").casefold())  # stable tie-break
    present.sort(key=value, reverse=descending)  # type: ignore[arg-type,return-value]
    missing.sort(key=lambda g: (g.title or "").casefold())
    return present + missing


def genre_counts(games: Sequence[GameSummary], limit: int = 12) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for game in games:
        for subject in game.subjects:
            folded = subject.casefold()
            counts[folded] = counts.get(folded, 0) + 1
            labels.setdefault(folded, subject)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [(labels[k], n) for k, n in ranked]


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------


class ArchiveTile(QFrame):
    """One poster: cover, bare title, one meta line."""

    clicked = Signal(str)

    def __init__(self, game: GameSummary) -> None:
        super().__init__()
        self.setObjectName("tile")
        self.game = game
        self.identifier = game.identifier
        self.setFixedWidth(COVER_W + 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("hover", False)
        self.setProperty("selected", False)
        self.setToolTip(f"{game.title}\n{game.identifier}")

        col = QVBoxLayout(self)
        col.setContentsMargins(8, 8, 8, 8)
        col.setSpacing(2)
        self.cover = CoverArt(COVER_W, COVER_H)
        col.addWidget(self.cover)

        title = QLabel(bare_title(game.title) or game.identifier)
        title.setObjectName("tile-title")
        title.setContentsMargins(2, 7, 0, 0)
        title.setMaximumWidth(COVER_W)
        fm = title.fontMetrics()
        title.setText(fm.elidedText(title.text(), Qt.ElideRight, COVER_W - 4))
        col.addWidget(title)

        meta_text = tile_meta(game)
        if meta_text:
            meta = QLabel(meta_text)
            meta.setObjectName("tile-meta")
            meta.setContentsMargins(2, 0, 0, 0)
            meta.setText(meta.fontMetrics().elidedText(meta_text, Qt.ElideRight, COVER_W - 4))
            col.addWidget(meta)
        col.addStretch(1)
        self._loaded = False

    def set_pixmap(self, pix: QPixmap) -> None:
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

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.identifier)
        super().mousePressEvent(event)


class FilterColumn(QFrame):
    """Left column: grouped chips with live counts (year, license, genre)."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("filters")
        self.setFixedWidth(216)
        self.year = "all"
        self.license = "all"
        self.genre = "all"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(16, 16, 16, 24)
        self._col.setSpacing(4)
        self._groups: dict[str, QButtonGroup] = {}
        self._genre_host = QVBoxLayout()
        self._genre_host.setSpacing(2)
        self._add_group("year", "RELEASE YEAR", YEAR_FILTERS)
        self._add_group("license", "LICENSE", LICENSE_FILTERS)
        head = QLabel("GENRE")
        head.setObjectName("filter-head")
        head.setContentsMargins(0, 14, 0, 4)
        self._col.addWidget(head)
        self._col.addLayout(self._genre_host)
        self._col.addStretch(1)
        self.set_genres([])

    def _chip(self, text: str, key: str, group: QButtonGroup, checked: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("chip")
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setProperty("key", key)
        group.addButton(btn)
        return btn

    def _add_group(self, attr: str, title: str, options: Sequence[tuple[str, str]]) -> None:
        head = QLabel(title)
        head.setObjectName("filter-head")
        head.setContentsMargins(0, 0 if attr == "year" else 14, 0, 4)
        self._col.addWidget(head)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, label in options:
            btn = self._chip(label, key, group, key == "all")
            self._col.addWidget(btn)
        group.buttonClicked.connect(lambda b, a=attr: self._pick(a, b))
        self._groups[attr] = group

    def _pick(self, attr: str, button: QPushButton) -> None:
        setattr(self, attr, str(button.property("key")))
        self.changed.emit()

    def set_genres(self, genres: Sequence[tuple[str, int]]) -> None:
        clear_layout(self._genre_host)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._groups["genre"] = group
        keep = self.genre
        self.genre = "all"
        for key, label in [("all", "All genres")] + [(g, f"{g}  ·  {n}") for g, n in genres]:
            on = key == keep or (key == "all" and keep not in {g for g, _ in genres})
            btn = self._chip(label, key, group, on)
            if on:
                self.genre = key
            self._genre_host.addWidget(btn)
        group.buttonClicked.connect(lambda b: self._pick("genre", b))


class ArchiveDetail(QFrame):
    """Right-hand panel for the selected archive game."""

    closed = Signal()
    download_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("detail")
        self.setFixedWidth(360)
        self._identifier = ""
        col = QVBoxLayout(self)
        col.setContentsMargins(18, 18, 18, 18)
        col.setSpacing(6)
        head = QHBoxLayout()
        eyebrow = QLabel("SELECTED GAME")
        eyebrow.setObjectName("detail-eyebrow")
        head.addWidget(eyebrow)
        head.addStretch(1)
        close = QToolButton()
        close.setObjectName("flat")
        close.setText("✕")
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        col.addLayout(head)
        self.cover = CoverArt(COVER_W, COVER_H)
        col.addWidget(self.cover, 0, Qt.AlignLeft)
        self.title = QLabel()
        self.title.setObjectName("detail-title")
        self.title.setWordWrap(True)
        self.title.setContentsMargins(0, 8, 0, 0)
        col.addWidget(self.title)
        self.sub = QLabel()
        self.sub.setObjectName("detail-sub")
        self.sub.setWordWrap(True)
        col.addWidget(self.sub)
        self.facts = QLabel()
        self.facts.setObjectName("detail-note")
        self.facts.setWordWrap(True)
        self.facts.setContentsMargins(0, 8, 0, 0)
        col.addWidget(self.facts)
        col.addStretch(1)
        self.download = QPushButton("Download")
        self.download.setObjectName("suggested")
        self.download.clicked.connect(lambda: self.download_requested.emit(self._identifier))
        col.addWidget(self.download)

    def show_game(self, game: GameSummary, pix: QPixmap | None) -> None:
        self._identifier = game.identifier
        self.title.setText(bare_title(game.title) or game.identifier)
        self.sub.setText(" · ".join(b for b in (game.creator, str(game.year or "")) if b)
                         or "No creator or year recorded")
        lines = []
        if game.item_size:
            lines.append(f"Size: {human_size(game.item_size)}")
        if game.downloads:
            lines.append(f"Downloads: {game.downloads:,}")
        if game.subjects:
            lines.append("Genres: " + ", ".join(game.subjects[:6]))
        lines.append(f"License: {dict(LICENSE_FILTERS)[license_group(game.licenseurl)]}")
        self.facts.setText("\n".join(lines))
        if pix is not None:
            self.cover.set_pixmap(pix)
        else:
            self.cover.clear()


class ArchiveView(QWidget):
    """Remote catalog browser."""

    download_requested = Signal(str)

    def __init__(self, client: ArchiveClient | None = None,
                 fetch_image: Callable[[GameSummary], bytes] | None = None) -> None:
        super().__init__()
        self.client = client
        self._fetch_image = fetch_image or self._default_fetch_image
        self._games: list[GameSummary] = []
        self._tiles: dict[str, ArchiveTile] = {}
        self._pix: dict[str, QPixmap] = {}
        self._selected = ""
        self._descending = False
        self._generation = 0

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.filters = FilterColumn()
        self.filters.changed.connect(self.refresh)
        root.addWidget(self.filters)

        mid = QVBoxLayout()
        mid.setSpacing(0)
        root.addLayout(mid, 1)

        bar = QFrame()
        bar.setObjectName("toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 4)
        row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the archive")
        self.search.addAction(search_icon(QColor(THEME_STATE["muted"])),
                              QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        self.sort = QComboBox()
        for key in ARCHIVE_SORT_KEYS:
            self.sort.addItem(f"Sort: {SORT_LABELS[key]}", key)
        self.sort.setMinimumWidth(170)
        self.sort.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.sort)
        self.direction = QToolButton()
        self.direction.setText("↑")
        self.direction.setToolTip("Sort direction")
        self.direction.clicked.connect(self._flip)
        row.addWidget(self.direction)
        self.reload_btn = QPushButton("↻")
        self.reload_btn.setToolTip("Reload the catalog")
        self.reload_btn.clicked.connect(lambda: self.load(force=True))
        row.addWidget(self.reload_btn)
        mid.addWidget(bar)

        self.status = QLabel("Loading the catalog…")
        self.status.setObjectName("usage")
        self.status.setContentsMargins(26, 0, 0, 6)
        mid.addWidget(self.status)
        line = QFrame()
        line.setObjectName("metabar")
        line.setFixedHeight(1)
        mid.addWidget(line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        self.flow = FlowLayout(host, spacing=4)
        self.flow.setContentsMargins(10, 16, 10, 24)
        self.scroll.setWidget(host)
        self.scroll.verticalScrollBar().valueChanged.connect(self._load_visible_covers)
        mid.addWidget(self.scroll, 1)

        self.empty = QLabel("")
        self.empty.setObjectName("empty-sub")
        self.empty.setAlignment(Qt.AlignCenter)
        mid.addWidget(self.empty, 1)
        self.empty.hide()

        self.detail = ArchiveDetail()
        self.detail.hide()
        self.detail.closed.connect(self.clear_selection)
        self.detail.download_requested.connect(self.download_requested.emit)
        root.addWidget(self.detail)

    # data ------------------------------------------------------------------
    def load(self, force: bool = False) -> None:
        if self.client is None:
            return
        client = self.client
        self.status.setText("Loading the catalog…")
        self.reload_btn.setEnabled(False)

        def done(result: object) -> None:
            self.reload_btn.setEnabled(True)
            self.set_games(result.games)  # type: ignore[attr-defined]
            warning = getattr(result, "warning", "")
            if warning:
                self.status.setText(f"{self.status.text()} · {warning}")

        def failed(exc: object) -> None:
            self.reload_btn.setEnabled(True)
            self.status.setText(str(exc) or "Could not load the catalog.")

        run_async(lambda: client.fetch_collection(force=force), done, failed)

    def set_games(self, games: Sequence[GameSummary]) -> None:
        self._games = list(games)
        self.filters.set_genres(genre_counts(self._games))
        self.refresh()

    def _flip(self) -> None:
        self._descending = not self._descending
        self.direction.setText("↓" if self._descending else "↑")
        self.refresh()

    def refresh(self) -> None:
        shown = sort_games(
            filter_games(
                self._games,
                query=self.search.text(),
                year=self.filters.year,
                license_=self.filters.license,
                genre=self.filters.genre,
            ),
            self.sort.currentData() or "title",
            self._descending,
        )
        self._generation += 1
        clear_layout(self.flow)
        self._tiles.clear()
        for game in shown:
            tile = ArchiveTile(game)
            tile.clicked.connect(self.select)
            if game.identifier in self._pix:
                tile.set_pixmap(self._pix[game.identifier])
            self._tiles[game.identifier] = tile
            self.flow.addWidget(tile)
        self.status.setText(f"{len(shown)} shown · {len(self._games)} games")
        has = bool(shown)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            self.empty.setText(
                "No games match these filters." if self._games
                else "The catalog is empty or could not be loaded."
            )
        if self._selected in self._tiles:
            self._tiles[self._selected].set_selected(True)
        self.flow.invalidate()
        self._load_visible_covers()

    # covers ----------------------------------------------------------------
    def _default_fetch_image(self, game: GameSummary) -> bytes:
        assert self.client is not None
        return self.client.fetch_image(thumb_url(game), f"thumb-{game.identifier}")

    def _load_visible_covers(self) -> None:
        """Fetch covers only for tiles near the viewport."""
        viewport = self.scroll.viewport().rect()
        top = self.scroll.verticalScrollBar().value() - 400
        bottom = top + viewport.height() + 800
        generation = self._generation
        for identifier, tile in list(self._tiles.items()):
            if tile._loaded or identifier in self._pix:
                continue
            if not (top <= tile.y() <= bottom):
                continue
            if not thumb_url(tile.game):
                continue
            tile._loaded = True
            run_async(
                lambda g=tile.game: self._fetch_image(g),
                lambda data, i=identifier, gen=generation: self._cover_ready(i, gen, data),
            )

    def _cover_ready(self, identifier: str, generation: int, data: object) -> None:
        pix = QPixmap()
        if not isinstance(data, (bytes, bytearray)) or not pix.loadFromData(bytes(data)):
            return
        self._pix[identifier] = pix
        tile = self._tiles.get(identifier)
        if tile is not None and generation == self._generation:
            tile.set_pixmap(pix)
        if identifier == self._selected:
            game = next((g for g in self._games if g.identifier == identifier), None)
            if game:
                self.detail.show_game(game, pix)

    # selection -------------------------------------------------------------
    def select(self, identifier: str) -> None:
        if self._selected in self._tiles:
            self._tiles[self._selected].set_selected(False)
        self._selected = identifier
        if identifier in self._tiles:
            self._tiles[identifier].set_selected(True)
        game = next((g for g in self._games if g.identifier == identifier), None)
        if game:
            self.detail.show_game(game, self._pix.get(identifier))
            self.detail.show()

    def clear_selection(self) -> None:
        if self._selected in self._tiles:
            self._tiles[self._selected].set_selected(False)
        self._selected = ""
        self.detail.hide()
