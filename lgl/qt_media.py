"""Movies & TV page for the Qt (Windows) frontend.

The design follows the GTK media window: a top bar with the brand, section links
and a search field, a cinematic hero banner, horizontal carousels, poster grids
for Movies / TV Shows / Anime, and My List / History built from local state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import mal, media_history, platform_support, streaming, tmdb
from .qt_icons import icon, paint_icon
from .qt_widgets import (
    CoverArt,
    ElideLabel,
    EmptyState,
    HeroBanner,
    IconButton,
    ScrollRow,
    SectionTitle,
    Toast,
    label,
    pixmap_from_bytes,
    cached_image_bytes,
    run_async,
    scroll_page,
)
from .settings import Settings
from .util import human_size  # noqa: F401 - kept for a stable import surface

WATCHLIST_KEY = "movie_watchlist"
NAV_ITEMS = (
    ("home", "Home"), ("movies", "Movies"), ("tv", "TV Shows"),
    ("anime", "Anime"), ("search", "Search"), ("list", "My List"),
    ("history", "History"),
)


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    """One title, normalised from a TMDB item or a MAL item."""

    key: str
    title: str
    year: str = ""
    poster_url: str = ""
    backdrop_url: str = ""
    overview: str = ""
    rating: float | None = None
    kind: str = "movie"       # movie | tv | anime
    tmdb_id: int = 0
    mal_id: int = 0

    @property
    def noun(self) -> str:
        return {"movie": "Movie", "tv": "TV", "anime": "Anime"}.get(self.kind, self.kind.title())

    @property
    def meta(self) -> str:
        bits = [bit for bit in (self.year, self.noun) if bit]
        return " · ".join(bits)


def card_from_tmdb(item: object) -> Card:
    return Card(
        key=str(getattr(item, "key", "")),
        title=str(getattr(item, "title", "") or "Untitled"),
        year=str(getattr(item, "year", "") or ""),
        poster_url=str(getattr(item, "poster_url", "") or ""),
        backdrop_url=str(getattr(item, "backdrop_url", "") or ""),
        overview=str(getattr(item, "overview", "") or ""),
        rating=getattr(item, "rating", None),
        kind=str(getattr(item, "kind", "movie") or "movie"),
        tmdb_id=int(getattr(item, "tmdb_id", 0) or 0),
    )


def card_from_anime(item: object) -> Card:
    return Card(
        key=str(getattr(item, "key", "")),
        title=str(getattr(item, "title", "") or "Untitled"),
        year=str(getattr(item, "year", "") or ""),
        poster_url=str(getattr(item, "poster_url", "") or ""),
        overview=str(getattr(item, "synopsis", "") or ""),
        rating=getattr(item, "rating", None),
        kind="anime",
        mal_id=int(getattr(item, "id", 0) or 0),
    )


def card_from_history(entry: dict) -> Card:
    key = str(entry.get("key", ""))
    kind, _, number = key.partition(":")
    return Card(
        key=key,
        title=str(entry.get("title", "") or "Untitled"),
        year=str(entry.get("year", "") or ""),
        poster_url=str(entry.get("poster_url", "") or ""),
        kind=kind or "movie",
        tmdb_id=int(number) if number.isdigit() else 0,
    )


def rating_text(rating: float | None) -> str:
    return f"{rating:.1f}" if isinstance(rating, (int, float)) and rating else ""


# --------------------------------------------------------------------------
# widgets
# --------------------------------------------------------------------------


class PosterCard(QFrame):
    """Poster (2:3) with title and meta below, used by grids and carousels."""

    clicked = Signal(object)

    def __init__(self, card: Card, width: int = 158) -> None:
        super().__init__()
        self.setObjectName("coverCard")
        self.setProperty("hover", False)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.card = card
        self._width = width
        self.setFixedWidth(width)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.cover = CoverArt(width, int(width * 1.5), radius=6, crop=True, title=card.title)
        column.addWidget(self.cover)

        self.title = ElideLabel(card.title)
        self.title.setObjectName("posterTitle")
        self.title.setContentsMargins(0, 8, 0, 0)
        self.title.setFixedWidth(width)
        column.addWidget(self.title)
        meta = ElideLabel(card.meta or "—")
        meta.setObjectName("posterMeta")
        meta.setFixedWidth(width)
        column.addWidget(meta)
        column.addStretch(1)
        self.setFixedHeight(int(width * 1.5) + 46)

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self.cover.set_pixmap(pix)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setProperty("hover", True)
        self.style().unpolish(self)
        self.style().polish(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setProperty("hover", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.card)
        super().mouseReleaseEvent(event)


class PosterGrid(QWidget):
    """Responsive poster grid (ten across on a wide window)."""

    item_clicked = Signal(object)

    def __init__(self, width: int = 158, min_column: int = 150) -> None:
        super().__init__()
        self._width = width
        self._min = min_column
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(22)
        self._cards: list[PosterCard] = []

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._cards.clear()

    def set_cards(self, cards: Sequence[Card]) -> None:
        self.clear()
        self._cards = [PosterCard(card, self._width) for card in cards]
        for poster in self._cards:
            poster.clicked.connect(self.item_clicked.emit)
        self._reflow(force=True)

    def cards(self) -> list[PosterCard]:
        return list(self._cards)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self, force: bool = False) -> None:
        width = max(self.width(), self._min)
        columns = max(1, int((width + 16) // (self._min + 16)))
        if not force and columns == getattr(self, "_columns", 0):
            return
        self._columns = columns
        for index, poster in enumerate(self._cards):
            self._grid.addWidget(poster, index // columns, index % columns)


class Carousel(QWidget):
    """A titled horizontal strip of posters with left/right arrows."""

    item_clicked = Signal(object)
    all_clicked = Signal()

    def __init__(self, title: str, count: str = "", note: str = "",
                 poster_width: int = 150, show_all: bool = False) -> None:
        super().__init__()
        row = QVBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.heading = SectionTitle(title, count, note)
        if show_all:
            view_all = QPushButton("View All")
            view_all.setObjectName("heroAction")
            view_all.setCursor(Qt.PointingHandCursor)
            view_all.clicked.connect(self.all_clicked.emit)
            self.heading.add_tail(view_all)
        row.addWidget(self.heading)

        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        self.left = _Arrow("chevron-left")
        self.left.clicked.connect(lambda: self.strip.scroll_by(-int(self.strip.width() * 0.8)))
        strip.addWidget(self.left, 0, Qt.AlignVCenter)
        self.strip = ScrollRow(int(poster_width * 1.5) + 52, spacing=16)
        strip.addWidget(self.strip, 1)
        self.right = _Arrow("chevron-right")
        self.right.clicked.connect(lambda: self.strip.scroll_by(int(self.strip.width() * 0.8)))
        strip.addWidget(self.right, 0, Qt.AlignVCenter)
        row.addLayout(strip)
        self._width = poster_width

    def set_cards(self, cards: Sequence[Card]) -> None:
        self.strip.clear()
        for card in cards:
            poster = PosterCard(card, self._width)
            poster.clicked.connect(self.item_clicked.emit)
            self.strip.add(poster)
        self.setVisible(bool(cards))

    def posters(self) -> Iterable[PosterCard]:
        layout = self.strip.widget().layout() if self.strip.widget() else None
        if layout is None:
            return []
        found = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if isinstance(widget, PosterCard):
                found.append(widget)
        return found


class _Arrow(QPushButton):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("carouselArrow")
        self.setFixedSize(32, 44)
        self.setCursor(Qt.PointingHandCursor)
        from .qt_widgets import color

        self.setIcon(icon(name, 16, color("muted"), 1.9))


class HeroPanel(HeroBanner):
    """Backdrop with the title block and actions laid over it."""

    play_clicked = Signal()
    details_clicked = Signal()
    list_clicked = Signal()

    def __init__(self) -> None:
        super().__init__(430)
        from PySide6.QtWidgets import QSpacerItem

        column = QVBoxLayout(self)
        column.setContentsMargins(64, 64, 64, 28)
        column.setSpacing(8)
        column.addStretch(1)
        self.kind = label("", "heroKind")
        column.addWidget(self.kind)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.star = label("", "heroMeta")
        row.addWidget(self.star)
        row.addStretch(1)
        column.addLayout(row)
        self.title = label("", "heroTitle", wrap=True)
        column.addWidget(self.title)
        self.meta = label("", "heroMeta")
        column.addWidget(self.meta)
        self.overview = label("", "heroOverview", wrap=True)
        self.overview.setMaximumWidth(720)
        column.addWidget(self.overview)
        column.addSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.play = QPushButton("▶  Watch Now")
        self.play.setObjectName("heroPlay")
        self.play.setCursor(Qt.PointingHandCursor)
        self.play.clicked.connect(self.play_clicked.emit)
        actions.addWidget(self.play)
        self.details = QPushButton("Details")
        self.details.setObjectName("heroAction")
        self.details.setCursor(Qt.PointingHandCursor)
        self.details.clicked.connect(self.details_clicked.emit)
        actions.addWidget(self.details)
        self.save = QPushButton("＋ My List")
        self.save.setObjectName("heroAction")
        self.save.setCursor(Qt.PointingHandCursor)
        self.save.clicked.connect(self.list_clicked.emit)
        actions.addWidget(self.save)
        actions.addStretch(1)
        column.addLayout(actions)

    def show_card(self, card: Card, *, in_list: bool = False) -> None:
        rating = rating_text(card.rating)
        self.kind.setText(card.noun.upper())
        self.star.setText(f"★  {rating}" if rating else "")
        self.title.setText(card.title)
        bits = [bit for bit in (card.year, card.noun) if bit]
        self.meta.setText(" · ".join(bits))
        self.overview.setText(card.overview or "")
        self.overview.setVisible(bool(card.overview))
        self.save.setText("✓ In My List" if in_list else "＋ My List")


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------


class PlayerDialog(QDialog):
    """Embedded player when Qt WebEngine is available, otherwise the browser."""

    def __init__(self, url: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle(title or "Play")
        self.resize(1120, 700)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        view = None
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

            view = QWebEngineView(self)
            view.setUrl(url)
        except Exception:  # noqa: BLE001 - WebEngine is optional in the build
            view = None
        if view is None:
            panel = QWidget()
            inner = QVBoxLayout(panel)
            inner.setContentsMargins(28, 28, 28, 24)
            inner.setSpacing(10)
            inner.addStretch(1)
            inner.addWidget(label("Playback opens in your browser", "pageTitle"))
            inner.addWidget(label(
                "This build has no embedded player, so the provider page opens in your "
                "default browser instead. Nothing is downloaded.",
                "detailNote", wrap=True,
            ))
            open_btn = QPushButton("Open in browser")
            open_btn.setObjectName("suggested")
            open_btn.clicked.connect(lambda: platform_support.open_url(url))
            inner.addWidget(open_btn, 0, Qt.AlignLeft)
            inner.addStretch(1)
            column.addWidget(panel, 1)
            platform_support.open_url(url)
        else:
            column.addWidget(view, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(16, 8, 16, 12)
        bottom.addStretch(1)
        bottom.addWidget(close)
        column.addLayout(bottom)


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------


class MediaPage(QWidget):
    """Movies & TV: Home, Movies, TV Shows, Anime, Search, My List, History."""

    configure_requested = Signal()

    def __init__(self, settings: Settings | None = None,
                 cache_root: Path | None = None) -> None:
        super().__init__()
        self.settings = settings
        self._cache_root = cache_root or platform_support.cache_dir()
        self._pix: dict[str, QPixmap] = {}
        self._section = "home"
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(380)
        self._search_timer.timeout.connect(self._run_search)
        self._tmdb_items: list[Card] = []
        self._anime_items: list[Card] = []
        self._detail_card: Card | None = None
        self._loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QFrame()
        top.setObjectName("mediaTopBar")
        bar = QHBoxLayout(top)
        bar.setContentsMargins(24, 12, 24, 12)
        bar.setSpacing(14)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        dot = _Dot()
        brand.addWidget(dot, 0, Qt.AlignVCenter)
        brand.addWidget(label("Movies & TV", "mediaBrand"))
        bar.addLayout(brand)

        bar.addStretch(1)
        self._nav: dict[str, QPushButton] = {}
        for key, text in NAV_ITEMS:
            btn = QPushButton(text)
            btn.setObjectName("mediaNav")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self._nav[key] = btn
            bar.addWidget(btn)
        bar.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search movies, TV, anime…")
        self.search.setFixedWidth(210)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_typed)
        self.search.returnPressed.connect(self._run_search)
        bar.addWidget(self.search)
        stop = IconButton("close", "Clear the search", 13, 30)
        stop.clicked.connect(lambda: self.search.clear())
        bar.addWidget(stop)
        root.addWidget(top)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self._pages: dict[str, QWidget] = {}
        for key, title in (("home", "Home"), ("movies", "Movies"), ("tv", "TV Shows"),
                           ("anime", "Anime"), ("search", "Search"),
                           ("list", "My List"), ("history", "History")):
            page, column = self._page()
            self._pages[key] = page
            self.stack.addWidget(page)
            if key == "home":
                self._build_home(column)
            elif key in ("movies", "tv"):
                self._build_grid_page(column, key)
            elif key == "anime":
                self._build_anime(column)
            elif key == "search":
                self._build_search(column)
            elif key == "list":
                self._build_list(column)
            else:
                self._build_history(column)
        self._detail_page, detail_column = self._page()
        self.stack.addWidget(self._detail_page)
        self._build_detail(detail_column)
        self.show_section("home")

    # scaffolding -----------------------------------------------------------
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        scroll, _body, column = scroll_page()
        column.setContentsMargins(24, 18, 24, 28)
        column.setSpacing(18)
        return scroll, column

    def _provider_card(self, title: str, body_text: str) -> QWidget:
        """Explain what is missing instead of showing an empty screen."""
        card = QFrame()
        card.setObjectName("popover")
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 16, 20, 16)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title, "sectionTitle"))
        text.addWidget(label(body_text, "detailNote", wrap=True))
        row.addLayout(text, 1)
        button = QPushButton("Open Settings")
        button.setObjectName("suggested")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(self.configure_requested.emit)
        row.addWidget(button, 0, Qt.AlignVCenter)
        return card

    def _section_head(self, column: QVBoxLayout, title: str, count: str = "",
                      note: str = "") -> SectionTitle:
        head = SectionTitle(title, count, note)
        column.addWidget(head)
        return head

    def _underline(self, column: QVBoxLayout) -> None:
        line = QFrame()
        line.setFixedHeight(2)
        line.setStyleSheet("background: rgba(232,234,237,0.10);")
        column.addWidget(line)

    # home ------------------------------------------------------------------
    def _build_home(self, column: QVBoxLayout) -> None:
        self._home_provider_card = self._provider_card(
            "Add a TMDB key to unlock Movies & TV",
            "Add a free TMDB API key in Settings → Providers. Anime also uses a "
            "MyAnimeList Client ID. Playback itself uses the provider you pick in "
            "Settings → Media.",
        )
        column.insertWidget(0, self._home_provider_card)
        self.hero = HeroPanel()
        column.insertWidget(1, self.hero)
        self.hero.play_clicked.connect(self._play_current)
        self.hero.details_clicked.connect(self._open_hero_details)
        self.hero.list_clicked.connect(self._toggle_current_list)
        self.hero.setVisible(False)

        self._home_carousels: dict[str, Carousel] = {}
        for key, title, note in (
            ("continue", "Continue Watching", "Recently watched"),
            ("trending", "Trending Now", "This week on TMDB"),
            ("movies", "Movies", "Popular on TMDB"),
            ("tv", "TV Shows", "Popular on TMDB"),
        ):
            carousel = Carousel(title, note=note, show_all=key in ("trending", "movies", "tv"))
            carousel.item_clicked.connect(self.open_card)
            if key == "trending":
                carousel.all_clicked.connect(lambda: self.show_section("movies"))
            elif key == "movies":
                carousel.all_clicked.connect(lambda: self.show_section("movies"))
            elif key == "tv":
                carousel.all_clicked.connect(lambda: self.show_section("tv"))
            carousel.setVisible(False)
            column.addWidget(carousel)
            self._home_carousels[key] = carousel

        column.addStretch(1)

    # movies / tv -----------------------------------------------------------
    def _build_grid_page(self, column: QVBoxLayout, key: str) -> None:
        head = self._section_head(column, "Movies" if key == "movies" else "TV Shows")
        note = label("Trending this week", "mediaNote")
        column.addWidget(note)
        self._underline(column)
        grid = PosterGrid()
        grid.item_clicked.connect(self.open_card)
        column.addWidget(grid)
        empty = EmptyState("media", "Nothing to show yet",
                           "A TMDB API key is needed for movies and TV metadata.")
        column.addWidget(empty)
        column.addStretch(1)
        self._pages_bits = getattr(self, "_pages_bits", {})
        self._pages_bits[key] = (head, grid, empty, note)

    # anime -----------------------------------------------------------------
    def _build_anime(self, column: QVBoxLayout) -> None:
        head = self._section_head(column, "Anime")
        column.addWidget(label(
            "Popular anime from MyAnimeList. Metadata comes from MAL; playback uses the "
            "anime provider selected in Settings. The main search also queries the "
            "ani.pm catalogue, where titles missing from MAL live.",
            "detailNote", wrap=True,
        ))
        self.anime_note = label("Popular now", "mediaNote")
        column.addWidget(self.anime_note)
        grid = PosterGrid()
        grid.item_clicked.connect(self.open_card)
        column.addWidget(grid)
        empty = EmptyState("media", "No anime list yet",
                           "Add a MyAnimeList Client ID in Settings → Providers.")
        column.addWidget(empty)
        column.addStretch(1)
        self._anime_head = head
        self._anime_grid = grid
        self._anime_empty = empty

    # search ----------------------------------------------------------------
    def _build_search(self, column: QVBoxLayout) -> None:
        head = self._section_head(column, "Search")
        self.search_note = label("Type a title above to search TMDB and MyAnimeList.", "mediaNote")
        column.addWidget(self.search_note)
        self.search_result_title = label("", "mediaNote")
        column.addWidget(self.search_result_title)
        grid = PosterGrid()
        grid.item_clicked.connect(self.open_card)
        column.addWidget(grid)
        empty = EmptyState(
            "search", "Search movies, TV and anime",
            "Results come straight from TMDB and the MAL catalogue — nothing is "
            "suggested or invented.",
        )
        column.addWidget(empty)
        column.addStretch(1)
        self._search_grid = grid
        self._search_empty = empty

    # my list ---------------------------------------------------------------
    def _build_list(self, column: QVBoxLayout) -> None:
        head = self._section_head(column, "My List")
        column.addWidget(label("Saved titles", "mediaNote"))
        grid = PosterGrid()
        grid.item_clicked.connect(self.open_card)
        column.addWidget(grid)
        empty = EmptyState("star", "Your list is empty",
                           "Save a title with “＋ My List” to find it here.")
        column.addWidget(empty)
        column.addStretch(1)
        self._list_head = head
        self._list_grid = grid
        self._list_empty = empty

    # history ---------------------------------------------------------------
    def _build_history(self, column: QVBoxLayout) -> None:
        self._section_head(column, "History")
        column.addWidget(label("Titles appear here only after you press Play.", "mediaNote"))
        grid = PosterGrid()
        grid.item_clicked.connect(self.open_card)
        column.addWidget(grid)
        self._history_empty = EmptyState(
            "media", "Nothing watched yet",
            "Continue Watching and History fill up when you play a title.",
        )
        column.addWidget(self._history_empty)
        column.addStretch(1)
        self._history_grid = grid

    # detail ----------------------------------------------------------------
    def _build_detail(self, column: QVBoxLayout) -> None:
        top = QHBoxLayout()
        self.detail_back = QPushButton("‹  Back")
        self.detail_back.setObjectName("heroAction")
        self.detail_back.setCursor(Qt.PointingHandCursor)
        self.detail_back.clicked.connect(lambda: self.show_section(self._section))
        top.addWidget(self.detail_back)
        top.addStretch(1)
        column.addLayout(top)
        self.detail_hero = HeroPanel()
        column.addWidget(self.detail_hero)
        self.detail_hero.play_clicked.connect(self._play_current)
        self.detail_hero.list_clicked.connect(self._toggle_current_list)
        self.detail_hero.details.clicked.connect(self._open_tmdb_page)
        self.detail_info = label("", "mediaNote", wrap=True)
        column.addWidget(self.detail_info)
        self.episode_head = SectionTitle("Episodes")
        column.addWidget(self.episode_head)
        self.episode_host = QWidget()
        self.episode_column = QVBoxLayout(self.episode_host)
        self.episode_column.setContentsMargins(0, 0, 0, 0)
        self.episode_column.setSpacing(4)
        column.addWidget(self.episode_host)
        column.addStretch(1)

    # navigation ------------------------------------------------------------
    def show_section(self, key: str) -> None:
        self._section = key
        for name, button in self._nav.items():
            button.setChecked(name == key)
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)
        if key == "list":
            self._render_list()
        elif key == "history":
            self._render_history()
        if not self._loaded:
            self._loaded = True
            QTimer.singleShot(0, self.reload)

    def reload(self) -> None:
        """Refresh every remote section; safe to call repeatedly."""
        self._render_list()
        self._render_history()
        if self.settings is None:
            return
        api_key = tmdb.load_api_key(settings=self.settings)
        client_id = mal.load_client_id(settings=self.settings)
        tmdb_client = tmdb.TmdbClient(api_key, timeout=float(self.settings.network_timeout))
        mal_client = mal.MalClient(client_id, timeout=float(self.settings.network_timeout))
        self._tmdb = tmdb_client
        self._mal = mal_client

        self._home_provider_card.setVisible(not tmdb_client.configured)
        self._pages_bits["movies"][2].setVisible(not tmdb_client.configured)
        self._pages_bits["tv"][2].setVisible(not tmdb_client.configured)
        self._anime_empty.setVisible(not mal_client.configured)
        if not tmdb_client.configured:
            self.hero.setVisible(False)
        else:
            self._load_home(tmdb_client)
        if mal_client.configured:
            self._load_anime(mal_client)

    def _load_home(self, client: object) -> None:
        def work() -> dict[str, object]:
            data: dict[str, object] = {}
            data["movies"] = client.trending("movie")  # type: ignore[attr-defined]
            data["tv"] = client.trending("tv")  # type: ignore[attr-defined]
            data["popular_movies"] = client.popular("movie")  # type: ignore[attr-defined]
            data["popular_tv"] = client.popular("tv")  # type: ignore[attr-defined]
            return data

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            movies = [card_from_tmdb(item) for item in data.get("movies", [])]  # type: ignore[union-attr]
            shows = [card_from_tmdb(item) for item in data.get("tv", [])]  # type: ignore[union-attr]
            popular_movies = [card_from_tmdb(item) for item in data.get("popular_movies", [])]  # type: ignore[union-attr]
            popular_tv = [card_from_tmdb(item) for item in data.get("popular_tv", [])]  # type: ignore[union-attr]
            self._tmdb_items = movies + shows
            hero_card = next((card for card in movies if card.backdrop_url), movies[0] if movies else None)
            if hero_card is not None:
                self._hero_card = hero_card
                self.hero.show_card(hero_card, in_list=self._in_list(hero_card.key))
                self.hero.setVisible(True)
                self._load_image(hero_card.backdrop_url, "backdrop", self.hero.set_pixmap)
            self._home_carousels["trending"].set_cards((movies + shows)[:20])
            self._home_carousels["movies"].set_cards(popular_movies)
            self._home_carousels["tv"].set_cards(popular_tv)
            self._home_carousels["movies"].heading.count.setText(str(len(popular_movies)))
            self._home_carousels["tv"].heading.count.setText(str(len(popular_tv)))
            self._home_carousels["trending"].heading.count.setText(str(len(movies + shows)))
            self._pages_bits["movies"][0].count.setText(str(len(movies)))
            self._pages_bits["tv"][0].count.setText(str(len(shows)))
            self._pages_bits["movies"][1].set_cards(movies)
            self._pages_bits["tv"][1].set_cards(shows)
            self._render_history()
            self._load_posters()

        run_async(work, done, lambda exc: self._note_error(exc))

    def _load_anime(self, client: object) -> None:
        def work() -> object:
            return client.ranking("bypopularity", limit=24)  # type: ignore[attr-defined]

        def done(result: object) -> None:
            cards = [card_from_anime(item) for item in result] if isinstance(result, list) else []
            self._anime_items = cards
            self._anime_head.count.setText(str(len(cards)))
            self.anime_note.setText(
                f"Popular now · {len(cards)} titles from MyAnimeList" if cards
                else "Popular now"
            )
            self._anime_grid.set_cards(cards)
            self._anime_empty.setVisible(not cards)
            self._load_posters()

        run_async(work, done, lambda exc: self._note_error(exc))

    def _note_error(self, exc: object) -> None:
        window = self.window()
        toast = getattr(window, "toast", None)
        if isinstance(toast, Toast):
            toast.show_message(str(exc), error=True)

    # posters ---------------------------------------------------------------
    def _load_posters(self) -> None:
        for card in self._all_cards():
            if not card.poster_url or card.key in self._pix:
                continue
            self._load_image(card.poster_url, f"poster-{card.key}", None)

    def _all_cards(self) -> list[Card]:
        cards: list[Card] = list(self._tmdb_items) + list(self._anime_items)
        for entry in self._history():
            cards.append(card_from_history(entry))
        for carousel in getattr(self, "_home_carousels", {}).values():
            for poster in carousel.posters():
                cards.append(poster.card)
        if hasattr(self, "_pages_bits"):
            for _head, grid, _empty, _note in self._pages_bits.values():
                for poster in grid.cards():
                    cards.append(poster.card)
        if hasattr(self, "_anime_grid"):
            for poster in self._anime_grid.cards():
                cards.append(poster.card)
        return cards

    def _load_image(self, url: str, key: str, apply: Callable[[QPixmap | None], None] | None) -> None:
        if not url:
            return
        if key in self._pix and apply is not None:
            apply(self._pix[key])
            return

        def work() -> bytes:
            return cached_image_bytes(url, self._cache_root, key, timeout=25.0)

        def done(data: object) -> None:
            pix = pixmap_from_bytes(data)
            if pix is None:
                return
            self._pix[key] = pix
            if apply is not None:
                apply(pix)
                return
            self._paint_card(url, pix)

        run_async(work, done, lambda _exc: None)

    def _paint_card(self, url: str, pix: QPixmap) -> None:
        for card in self._all_cards():
            if card.poster_url != url:
                continue
            for carousel in getattr(self, "_home_carousels", {}).values():
                for poster in carousel.posters():
                    if poster.card.key == card.key:
                        poster.set_pixmap(pix)
            if hasattr(self, "_pages_bits"):
                for _head, grid, _empty, _note in self._pages_bits.values():
                    for poster in grid.cards():
                        if poster.card.key == card.key:
                            poster.set_pixmap(pix)
            if hasattr(self, "_anime_grid"):
                for poster in self._anime_grid.cards():
                    if poster.card.key == card.key:
                        poster.set_pixmap(pix)
            for poster in list(self._list_grid.cards()) + list(self._history_grid.cards()):
                if poster.card.key == card.key:
                    poster.set_pixmap(pix)

    # search ----------------------------------------------------------------
    def _on_search_typed(self, _text: str) -> None:
        self.show_section_search()
        self._search_timer.start()

    def show_section_search(self) -> None:
        if self._section != "search":
            self._section = "search"
            for name, button in self._nav.items():
                button.setChecked(name == "search")
            self.stack.setCurrentWidget(self._pages["search"])

    def _run_search(self) -> None:
        query = self.search.text().strip()
        if not query:
            self._search_grid.set_cards([])
            self._search_empty.setVisible(True)
            self.search_result_title.setText("")
            return
        if self.settings is None:
            return
        self._search_empty.setVisible(False)
        self.search_note.setText(f"Searching for “{query}”…")
        tmdb_client = getattr(self, "_tmdb", None)
        mal_client = getattr(self, "_mal", None)

        def work() -> dict[str, list[object]]:
            found: dict[str, list[object]] = {"tmdb": [], "anime": [], "errors": []}
            if tmdb_client is not None and tmdb_client.configured:
                try:
                    found["tmdb"] = tmdb_client.search(query)
                except Exception as exc:  # noqa: BLE001 - reported below
                    found["errors"].append(f"TMDB: {exc}")  # type: ignore[union-attr]
            if mal_client is not None and mal_client.configured:
                try:
                    found["anime"] = mal_client.search(query)
                except Exception as exc:  # noqa: BLE001
                    found["errors"].append(f"MyAnimeList: {exc}")  # type: ignore[union-attr]
            return found

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            cards = [card_from_tmdb(item) for item in data.get("tmdb", [])]  # type: ignore[union-attr]
            cards += [card_from_anime(item) for item in data.get("anime", [])]  # type: ignore[union-attr]
            self._search_grid.set_cards(cards)
            self._search_empty.setVisible(not cards)
            self.search_note.setText(
                f"“{query}” · {len(cards)} result{'s' if len(cards) != 1 else ''}"
                if cards else f"No results for “{query}”."
            )
            self._load_posters()

        run_async(work, done, lambda exc: self._note_error(exc))

    # watchlist / history ---------------------------------------------------
    def _watchlist(self) -> list[str]:
        if self.settings is None:
            return []
        raw = self.settings.get(WATCHLIST_KEY, [])
        return [str(item) for item in raw] if isinstance(raw, list) else []

    def _in_list(self, key: str) -> bool:
        return key in self._watchlist()

    def _toggle_current_list(self) -> None:
        card = getattr(self, "_hero_card", None) or self._detail_card
        if card is None or self.settings is None:
            return
        entries = self._watchlist()
        if card.key in entries:
            entries.remove(card.key)
        else:
            entries.insert(0, card.key)
        self.settings.set(WATCHLIST_KEY, entries)
        self.hero.save.setText("✓ In My List" if card.key in entries else "＋ My List")
        if self._detail_card is not None:
            self.detail_hero.save.setText(
                "✓ In My List" if card.key in entries else "＋ My List"
            )
        self._render_list()
        window = self.window()
        toast = getattr(window, "toast", None)
        if isinstance(toast, Toast):
            toast.show_message(
                "Added to My List." if card.key in entries else "Removed from My List."
            )

    def _lookup(self, key: str) -> Card | None:
        for card in self._all_cards():
            if card.key == key:
                return card
        for entry in self._history():
            if str(entry.get("key")) == key:
                return card_from_history(entry)
        return None

    def _render_list(self) -> None:
        keys = self._watchlist()
        cards = [card for card in (self._lookup(key) for key in keys) if card]
        self._list_head.count.setText(str(len(cards)))
        self._list_grid.set_cards(cards)
        self._list_empty.setVisible(not cards)
        self._load_posters()

    def _history(self) -> list[dict]:
        if self.settings is None:
            return []
        return media_history.load(self.settings)

    def _render_history(self) -> None:
        entries = self._history()
        cards = [card_from_history(entry) for entry in entries]
        self._history_grid.set_cards(cards)
        self._history_empty.setVisible(not cards)
        carousel = getattr(self, "_home_carousels", {}).get("continue")
        if carousel is not None:
            carousel.set_cards(cards[:14])
        self._load_posters()

    # detail / playback -----------------------------------------------------
    def open_card(self, card: object) -> None:
        if not isinstance(card, Card):
            return
        self._detail_card = card
        self.detail_hero.show_card(card, in_list=self._in_list(card.key))
        self.detail_hero.details.setText("TMDB page")
        self.detail_hero.details.setVisible(card.kind != "anime")
        rating = rating_text(card.rating)
        self.detail_info.setText(
            (f"★ {rating}   " if rating else "")
            + card.meta
            + (f"\n\n{card.overview}" if card.overview else "")
        )
        self.stack.setCurrentWidget(self._detail_page)
        self._load_image(card.backdrop_url or card.poster_url, f"backdrop-{card.key}",
                         self.detail_hero.set_pixmap)
        self._load_episodes(card)

    def _load_episodes(self, card: Card) -> None:
        while self.episode_column.count():
            item = self.episode_column.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        client = getattr(self, "_tmdb", None)
        if card.kind != "tv" or card.tmdb_id == 0 or client is None:
            self.episode_head.setVisible(False)
            self.episode_host.setVisible(False)
            return
        self.episode_head.setVisible(True)
        self.episode_host.setVisible(True)
        self.episode_head.title.setText("Episodes")
        self.episode_head.count.setText("loading…")

        def work() -> object:
            return client.details("tv", card.tmdb_id)

        def done(result: object) -> None:
            seasons = list(getattr(result, "seasons", ()) or ())
            self.episode_head.count.setText(f"{len(seasons)} seasons")
            if not seasons:
                self.episode_column.addWidget(label("No episodes listed.", "mediaNote"))
                return
            for season in seasons[:6]:
                button = QPushButton(
                    f"Season {getattr(season, 'number', '?')} · "
                    f"{getattr(season, 'episode_count', 0)} episodes"
                )
                button.setObjectName("mediaNav")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(
                    lambda _=False, s=season, c=card: self._open_season(c, s)
                )
                self.episode_column.addWidget(button)

        run_async(work, done, lambda _exc: None)

    def _open_season(self, card: Card, season: object) -> None:
        client = getattr(self, "_tmdb", None)
        number = int(getattr(season, "number", 1) or 1)
        if client is None:
            return
        window = self.window()
        toast = getattr(window, "toast", None)
        if isinstance(toast, Toast):
            toast.show_message(f"Loading season {number}…", ms=2000)

        def work() -> object:
            return client.season(card.tmdb_id, number)

        def done(result: object) -> None:
            while self.episode_column.count():
                item = self.episode_column.takeAt(0)
                widget = item.widget() if item else None
                if widget is not None:
                    widget.deleteLater()
            episodes = list(getattr(result, "episodes", ()) or ())
            self.episode_head.count.setText(f"Season {number} · {len(episodes)} episodes")
            for episode in episodes[:40]:
                row = QPushButton(
                    f"{getattr(episode, 'number', '?')}. {getattr(episode, 'title', 'Episode')}"
                )
                row.setObjectName("mediaNav")
                row.setCursor(Qt.PointingHandCursor)
                row.clicked.connect(
                    lambda _=False, e=episode, c=card, s=number: self._play_tmdb(c, s, e)
                )
                self.episode_column.addWidget(row)

        run_async(work, done, lambda exc: self._note_error(exc))

    def _play_current(self) -> None:
        card = getattr(self, "_hero_card", None) or self._detail_card
        if card is None:
            return
        if card.kind == "anime":
            self._play_anime(card)
        else:
            self._play_tmdb(card)

    def _provider(self) -> str:
        default = streaming.DEFAULT_PROVIDER
        if self.settings is None:
            return default
        return streaming.clean_provider(self.settings.get("streaming_provider", default))

    def _anime_provider(self) -> str:
        if self.settings is None:
            return streaming.DEFAULT_ANIME_PROVIDER
        return streaming.clean_anime_provider(
            self.settings.get("anime_streaming_provider",
                              self.settings.get("streaming_provider",
                                                streaming.DEFAULT_ANIME_PROVIDER))
        )

    def _language(self) -> str:
        if self.settings is None:
            return "sub"
        return streaming.clean_language(
            "ani", self.settings.get("anime_language", "") or
            ("dub" if self.settings.get("anime_dub", False) else "sub"),
        )

    def _play_tmdb(self, card: Card, season: int | None = None, episode: object | None = None) -> None:
        if card.tmdb_id <= 0:
            self._note_error("This title has no TMDB id, so it cannot be played.")
            return
        provider = self._provider()
        try:
            if card.kind == "tv":
                url = streaming.episode_url(
                    provider, card.tmdb_id, season or 1,
                    getattr(episode, "number", 1), title=card.title,
                )
            else:
                url = streaming.movie_url(provider, card.tmdb_id, title=card.title)
        except (streaming.EmbedUrlError, ValueError) as exc:
            self._note_error(str(exc))
            return
        self._record_history(card)
        PlayerDialog(url, card.title, self).exec()

    def _play_anime(self, card: Card) -> None:
        provider = self._anime_provider()
        try:
            if provider == streaming.ANI_PROVIDER and card.mal_id:
                url = streaming.ani_mal_url(card.mal_id, 1, language=self._language())
            elif card.mal_id and provider in streaming.ANILIST_ANIME_PROVIDERS:
                anilist_id = streaming.anilist_search(card.title)
                if not anilist_id:
                    self._note_error("No AniList id matched this title.")
                    return
                url = streaming.anime_url(provider, anilist_id, 1)
            else:
                self._note_error(
                    "This anime title has no MyAnimeList id, so the anime provider "
                    "cannot be used. Pick ani.pm in Settings → Media."
                )
                return
        except (streaming.EmbedUrlError, ValueError) as exc:
            self._note_error(str(exc))
            return
        self._record_history(card)
        PlayerDialog(url, card.title, self).exec()

    def _record_history(self, card: Card) -> None:
        if self.settings is None or card.key.startswith("anime:"):
            media_history.record(
                self.settings, key=f"movie:{card.tmdb_id}", title=card.title,
                poster_url=card.poster_url, year=card.year,
            ) if card.tmdb_id else None
        else:
            media_history.record(
                self.settings, key=card.key, title=card.title,
                poster_url=card.poster_url, year=card.year,
            )
        if self.settings is not None:
            self.settings.set("media_last_played", time.time())
        self._render_history()

    def _open_hero_details(self) -> None:
        card = getattr(self, "_hero_card", None)
        if card is not None:
            self.open_card(card)

    def _open_tmdb_page(self) -> None:
        card = getattr(self, "_hero_card", None) or self._detail_card
        if card is not None and card.tmdb_id:
            platform_support.open_url(f"https://www.themoviedb.org/{card.kind}/{card.tmdb_id}")


class _Dot(QWidget):
    """The small amber dot in front of the "Movies & TV" wordmark."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(12, 12)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#f5b301"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(2, 2, 8, 8))


__all__ = [
    "Carousel",
    "Card",
    "HeroPanel",
    "MediaPage",
    "PlayerDialog",
    "PosterCard",
    "PosterGrid",
    "card_from_anime",
    "card_from_history",
    "card_from_tmdb",
    "rating_text",
]
