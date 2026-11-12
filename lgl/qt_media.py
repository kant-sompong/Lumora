"""Movies & TV page for the Qt (Windows) frontend.

The design follows the GTK media window: a top bar with the brand, section links
and a search field, a cinematic hero banner, horizontal carousels, poster grids
for Movies / TV Shows / Anime, and My List / History built from local state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from math import ceil
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import (
    mal,
    media_history,
    platform_support,
    provider_health,
    streaming,
    tmdb,
    trailer_data,
    trailers,
    view_prefs,
)
from . import recently_viewed
from .qt_icons import icon
from .qt_feed import PosterRow, ScheduleStrip, ThreeColumnList
from .qt_library import ArtworkBackdrop
from .qt_player import EmbeddedPlayer, make_player
from .qt_widgets import (
    CoverArt,
    ElideLabel,
    EmptyState,
    HeroBanner,
    IconButton,
    ScrollRow,
    SectionTitle,
    Toast,
    clear_layout,
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
    ("anime", "Anime"), ("list", "My List"), ("history", "History"),
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
        self.actions = actions
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


class EpisodeRow(QFrame):
    """One episode in the season list: number, title, air date, play hint."""

    clicked = Signal()

    def __init__(self, number: int, title: str, air_date: str = "") -> None:
        super().__init__()
        self.setObjectName("episodeRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 9, 14, 9)
        row.setSpacing(12)

        badge = QLabel(f"{int(number):02d}" if number else "—")
        badge.setObjectName("episodeNumber")
        badge.setFixedWidth(28)
        row.addWidget(badge, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        name = ElideLabel(title or "Episode")
        name.setObjectName("episodeTitle")
        text.addWidget(name)
        if air_date:
            meta = QLabel(str(air_date))
            meta.setObjectName("episodeMeta")
            text.addWidget(meta)
        row.addLayout(text, 1)

        play = QLabel("▶")
        play.setObjectName("episodePlay")
        row.addWidget(play, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class TrailerDialog(QDialog):
    """A trailer in the embedded WebEngine view, or a browser button.

    Nothing opens automatically when this build
    has no WebEngine: the user presses the button themselves.
    """

    def __init__(self, url: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setWindowTitle(f"Trailer · {title}" if title else "Trailer")
        self.resize(1120, 700)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        view = None
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings  # type: ignore
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

            view = QWebEngineView(self)
            page = view.page()
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
            )
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
            )
            page.fullScreenRequested.connect(lambda request: request.accept())
            page.setBackgroundColor(QColor("#000000"))
            view.setUrl(url)
        except Exception:  # noqa: BLE001 - WebEngine is optional in the build
            view = None
        if view is None:
            panel = QWidget()
            inner = QVBoxLayout(panel)
            inner.setContentsMargins(28, 28, 28, 24)
            inner.setSpacing(10)
            inner.addStretch(1)
            inner.addWidget(label("Open trailer in your browser", "pageTitle"))
            inner.addWidget(label(
                "This build has no embedded browser, so the trailer cannot play "
                "here. Nothing opens until you press the button.",
                "detailNote", wrap=True,
            ))
            open_btn = QPushButton("Open trailer in your browser")
            open_btn.setObjectName("suggested")
            open_btn.clicked.connect(lambda: platform_support.open_url(url))
            inner.addWidget(open_btn, 0, Qt.AlignLeft)
            inner.addStretch(1)
            column.addWidget(panel, 1)
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
    surprise_requested = Signal()
    #: Emitted from the health-check worker thread; Qt queues it to the GUI.
    health_ready = Signal(list)

    def __init__(self, settings: Settings | None = None,
                 cache_root: Path | None = None,
                 player_factory: Callable[[QWidget], EmbeddedPlayer] | None = None) -> None:
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
        self._artwork = ArtworkBackdrop(self, settings)

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

        # One button: the current provider and its ping.  The menu behind it
        # lists Auto, every provider of the active list, and Test connections.
        self.provider_button = ProviderButton(self)
        self.provider_button.clicked.connect(self._show_provider_menu)
        bar.addWidget(self.provider_button)
        self.health_badge = self.provider_button  # kept name: same single button
        # The AniList lookup is part of the search field now (as on Linux):
        # typing a title searches TMDB, MyAnimeList and AniList at once, so a
        # second "Lookup…" box beside it only asked for the same title twice.
        self._fill_provider_menu("home")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Movies & TV")
        self.search.setMinimumWidth(150)
        self.search.setMaximumWidth(240)
        self.search.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_typed)
        self.search.returnPressed.connect(self._run_search)
        bar.addWidget(self.search)
        stop = IconButton("close", "Clear the search", 13, 30)
        stop.clicked.connect(lambda: self.search.clear())
        bar.addWidget(stop)
        root.addWidget(top)

        # Provider health: one compact strip, then the measured list of
        # providers below it.  Nothing here starts a request by itself.
        self.health_results: list[dict] = []
        self.health_bar = QFrame()
        self.health_bar.setObjectName("mediaHealthBar")
        health_outer = QVBoxLayout(self.health_bar)
        health_outer.setContentsMargins(24, 6, 24, 8)
        health_outer.setSpacing(6)
        health_controls = QHBoxLayout()
        health_controls.setSpacing(10)
        self.health_btn = QPushButton("Test connection")
        self.health_btn.setObjectName("suggested")
        self.health_btn.setCursor(Qt.PointingHandCursor)
        self.health_btn.setToolTip("Contact each provider once and measure the reply")
        self.health_btn.clicked.connect(self.test_connection)
        self.health_refresh = QPushButton("Refresh")
        self.health_refresh.setCursor(Qt.PointingHandCursor)
        self.health_refresh.setToolTip("Force a new health check")
        self.health_refresh.clicked.connect(self.refresh_health)
        self.fastest_btn = QPushButton("Use fastest")
        self.fastest_btn.setCursor(Qt.PointingHandCursor)
        self.fastest_btn.setToolTip("Select the quickest reachable provider")
        self.fastest_btn.clicked.connect(self.use_fastest)
        self.health_note = label("", "mediaNote")
        health_controls.addWidget(self.health_btn)
        health_controls.addWidget(self.health_refresh)
        health_controls.addWidget(self.fastest_btn)
        health_controls.addWidget(self.health_note, 1)
        health_outer.addLayout(health_controls)
        self.health_list = QWidget()
        self.health_rows = QVBoxLayout(self.health_list)
        self.health_rows.setContentsMargins(0, 0, 0, 0)
        self.health_rows.setSpacing(2)
        self.health_list.setVisible(False)
        health_outer.addWidget(self.health_list)
        # The measured list belongs to the page, not to the strip: the strip is
        # gone and the list opens from the badge menu instead.
        self.health_list.setParent(self)
        self.health_bar.setVisible(False)  # the badge in the header replaces the strip
        root.addWidget(self.health_bar)
        self.health_ready.connect(self._apply_health)
        self.health = provider_health.AutoSelect(
            settings, on_results=self.health_ready.emit, token="qt-media"
        )
        if settings is not None:
            QTimer.singleShot(3000, self._startup_health)

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
        # The player is a page of this Media page, not a dialog: Watch swaps the
        # stack, so the window never closes and nothing flickers.
        self._player_factory = player_factory or make_player
        self.player = self._player_factory(self)
        self.player.back_requested.connect(self._leave_player)
        self.stack.addWidget(self.player)
        self._player_return: QWidget | None = None
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

        # The home page is one long scroll of poster rows, the way a streaming
        # site reads.  Every row owns its own loader, so a slow or failed
        # section never holds up the page or the rows after it.
        self._home_rows: dict[str, PosterRow] = {}
        self.home_loaders: dict[str, Callable[[], object]] = {}
        sections = (
            ("continue", "Continue Watching", "Pick up where you left off", None, False),
            ("trending", "Trending Now", "This week on TMDB", "movies", True),
            ("movies", "Movies", "Popular on TMDB", "movies", True),
            ("tv", "TV Shows", "Popular on TMDB", "tv", True),
            ("for_you", "For you", "From what you watch and save", None, False),
            # Hidden gems and Anime gems read the same AniList list, so they are
            # merged into this one row instead of showing the same titles twice.
            ("hidden_gems", "Hidden gems",
             "Anime · high scores, few viewers (Anime gems)", "anime", True),
            ("more_like", "More like this", "Because of what you watched last", None, False),
        )
        for key, title, note, target, show_all in sections:
            row = PosterRow(
                title, note,
                poster_factory=lambda card: PosterCard(card, 150),
                poster_width=150,
                view_all=show_all,
            )
            row.item_clicked.connect(self.open_card)
            # A row fills its strip asynchronously; ask for every poster once
            # the cards exist, otherwise the strip stays blank until something
            # else happens to call _load_posters().
            row.cards_set.connect(self._load_posters)
            if target:
                row.view_all_clicked.connect(
                    lambda _checked=False, section=target: self.show_section(section)
                )
            row.setVisible(False)
            column.addWidget(row)
            self._home_rows[key] = row
        self._home_carousels = self._home_rows  # kept: history and posters read it

        # Section 7: three vertical lists, five small rows each.
        self.three_columns = ThreeColumnList(image_loader=self._load_image)
        column.addWidget(self.three_columns)
        self.column_loaders: dict[str, Callable[[], object]] = {}
        self.columns_placeholder = label("", "mediaNote")
        self.columns_placeholder.setVisible(False)
        column.addWidget(self.columns_placeholder)

        # Section 8: the day strip and the airing list for the chosen day.
        self.schedule = ScheduleStrip()
        column.addWidget(self.schedule)
        self.schedule_error = label("", "mediaNote", wrap=True)
        self.schedule_error.setVisible(False)
        column.addWidget(self.schedule_error)
        self.schedule_loader: Callable[[], object] | None = None
        self.schedule.setVisible(False)

        column.addStretch(1)

    # sections 7-9 ----------------------------------------------------------
    def set_column_loaders(self, loaders: dict[str, Callable[[], object]] | None = None,
                           **extra: Callable[[], object]) -> None:
        merged = dict(loaders or {})
        merged.update(extra)
        for key, loader in merged.items():
            if key in self.three_columns.columns and callable(loader):
                self.column_loaders[key] = loader

    def refresh_columns(self) -> None:
        """Load the three lists together, one worker, one error line per column."""
        if not self.column_loaders:
            return
        loaders = dict(self.column_loaders)

        def work() -> dict[str, object]:
            found: dict[str, object] = {}
            for key, loader in loaders.items():
                try:
                    found[key] = loader()
                except Exception as exc:  # noqa: BLE001 - becomes the column line
                    found[key] = exc
            return found

        def done(result: object) -> None:
            data = result if isinstance(result, dict) else {}
            for key, value in data.items():
                if isinstance(value, Exception):
                    self.three_columns.set_error(key, value)
                else:
                    self.three_columns.set_items(key, value)  # type: ignore[arg-type]
            self.columns_placeholder.setVisible(False)

        self.columns_placeholder.setText("Loading…")
        self.columns_placeholder.setVisible(True)
        run_async(work, done, lambda exc: self._columns_failed(exc))

    def _columns_failed(self, exc: object) -> None:
        self.columns_placeholder.setVisible(False)
        for key in self.column_loaders:
            self.three_columns.set_error(key, exc)

    def set_schedule_loader(self, loader: Callable[[], object] | None) -> None:
        self.schedule_loader = loader

    def refresh_schedule(self) -> None:
        if self.schedule_loader is None:
            return
        loader = self.schedule_loader
        self.schedule.setVisible(False)
        self.schedule_error.setVisible(False)

        def done(result: object) -> None:
            entries = [entry for entry in (result or [])]  # type: ignore[union-attr]
            if not entries:
                self.schedule.setVisible(False)
                return
            self.schedule.set_entries(entries)
            self.schedule.setVisible(True)

        def failed(exc: object) -> None:
            self.schedule.setVisible(False)
            self.schedule_error.setText(f"Could not load: {exc}")
            self.schedule_error.setVisible(True)

        run_async(lambda: loader(), done, failed)

    def _install_default_block_loaders(self) -> None:
        """New Release / New Added / Just Completed and the schedule."""
        from . import hidden_gems, release_feed

        def feed():
            getter = None
            if self.settings is not None:
                getter = release_feed.tmdb_getter(self.settings)
            return release_feed.load_feed(release_feed.default_cache_path(), tmdb_get=getter)

        def new_release() -> list[dict[str, object]]:
            now = time.time()
            aired = [
                entry for entry in feed().entries
                if entry.air_at is not None and entry.air_at <= now
            ]
            aired.sort(key=lambda entry: entry.air_at or 0.0, reverse=True)
            return [
                {
                    "title": entry.title,
                    "meta": f"{entry.source} · {time.strftime('%d %b', time.localtime(entry.air_at or 0))}",
                    "key": entry.key,
                }
                for entry in aired[:5]
            ]

        def lists() -> dict[str, object]:
            return hidden_gems.load_anime_lists()

        def list_rows(key: str) -> list[dict[str, object]]:
            block = lists()
            rows = block.get(key) if isinstance(block, dict) else None
            return [
                {
                    "title": str(row.get("title") or "Untitled"),
                    "meta": " · ".join(
                        part for part in (
                            str(row.get("format") or ""),
                            str(row.get("end_date") or ""),
                        ) if part
                    ),
                    "key": f"anime:{row.get('id')}",
                }
                for row in (rows or [])[:5]
            ]

        def schedule() -> list[object]:
            result = feed()
            if not result.entries and result.errors:
                raise RuntimeError("\n".join(result.errors))
            return list(result.entries)

        self.set_column_loaders({
            "new_release": new_release,
            "new_added": lambda: list_rows("new_added"),
            "just_completed": lambda: list_rows("just_completed"),
        })
        self.set_schedule_loader(schedule)

    def set_home_loaders(self, loaders: dict[str, Callable[[], object]] | None = None,
                         **extra: Callable[[], object]) -> None:
        """Inject a loader per home section (tests use this instead of the network)."""
        merged = dict(loaders or {})
        merged.update(extra)
        for key, loader in merged.items():
            row = self._home_rows.get(key)
            if row is not None and callable(loader):
                self.home_loaders[key] = loader
                row.set_loader(loader)

    def refresh_home(self) -> None:
        """Start every section that has a loader; each one hides itself on empty."""
        for row in self._home_rows.values():
            if row.loader() is not None:
                row.refresh()

    @staticmethod
    def _card_from_gem(entry: object) -> Card:
        """A hidden-gems entry as a poster card (AniList ids stand in for MAL ids)."""
        data = entry if isinstance(entry, dict) else {}
        ident = int(data.get("id") or 0)
        score = data.get("score")
        try:
            rating = float(score) / 10 if score is not None else None
        except (TypeError, ValueError):
            rating = None
        return Card(
            key=f"anime:{ident}",
            title=str(data.get("title") or "Untitled"),
            poster_url=str(data.get("poster_url") or ""),
            # The gems feed carries no wide art; the detail page uses the
            # poster so the hero still shows the title.
            backdrop_url=str(data.get("poster_url") or ""),
            kind="anime",
            mal_id=ident,
            rating=rating,
        )

    def _install_default_home_loaders(self) -> None:
        """For you / Hidden gems / More like this, from the loaded TMDB pool."""
        from . import discovery, hidden_gems

        saved = set(self._watchlist()) | {
            str(entry.get("key")) for entry in self._history()
        }

        def pool() -> list[dict[str, object]]:
            return [
                {
                    "key": card.key,
                    "title": card.title,
                    "kind": card.kind,
                    # TMDB shelf items carry no genres; the kind is the one
                    # shared label the similarity logic can use offline.
                    "genres": [card.kind],
                }
                for card in self._tmdb_items
            ]

        def to_cards(items: list[object]) -> list[Card]:
            cards = [self.card(str(item.get("key"))) for item in items if isinstance(item, dict)]
            return [card for card in cards if card is not None]

        def for_you() -> list[Card]:
            if not saved:
                return [
                    card for card in self._tmdb_items
                    if card.key not in {entry.get("key") for entry in self._history()}
                ][:18]
            return to_cards(discovery.recommend(pool(), saved))

        def more_like() -> list[Card]:
            seeds = [str(entry.get("key")) for entry in self._history()] + list(self._watchlist())
            if not seeds:
                return []
            return to_cards(discovery.similar(pool(), seeds[0]))

        def gems() -> list[Card]:
            result = hidden_gems.load_gems(excluded=saved)
            if not result.entries and result.error:
                raise RuntimeError(result.error)
            return [self._card_from_gem(entry) for entry in result.entries]

        self.set_home_loaders(for_you=for_you, hidden_gems=gems, more_like=more_like)

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
            "anime provider picked in the bar above (or Settings → Media). The main "
            "search also queries the ani.pm catalogue, where titles missing from MAL live.",
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
        self.anilist_btn = QPushButton("Play on AniList…")
        self.anilist_btn.setObjectName("suggested")
        self.anilist_btn.setCursor(Qt.PointingHandCursor)
        self.anilist_btn.setToolTip(
            "Play the AniList match for this search, for titles with no TMDB or "
            "MyAnimeList id"
        )
        self.anilist_btn.setVisible(False)
        self.anilist_btn.clicked.connect(self._play_anilist_match)
        self._anilist_match: tuple[str, int] | None = None
        column.addWidget(self.anilist_btn, 0, Qt.AlignLeft)
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
        # Hidden until TMDB says this title has a trailer we can embed. It
        # sits in the hero action row with Watch Now and My List, not beside
        # Back, where a viewing action does not belong.
        self.trailer_btn = QPushButton("▶  Trailer")
        self.trailer_btn.setObjectName("heroAction")
        self.trailer_btn.setCursor(Qt.PointingHandCursor)
        self.trailer_btn.setVisible(False)
        self.trailer_btn.clicked.connect(self._open_trailer)
        self.detail_hero.actions.insertWidget(1, self.trailer_btn)
        # Only a lookup that failed says so; a title with no trailer stays
        # quiet, because "no trailer" is not an error.
        self.trailer_note = label("Trailer not available", "detailNote")
        self.trailer_note.setVisible(False)
        column.addWidget(self.trailer_note)
        self.detail_hero.play_clicked.connect(self._play_detail)
        self.detail_hero.list_clicked.connect(self._toggle_detail_list)
        self.detail_hero.details.clicked.connect(self._open_tmdb_page)
        self.detail_info = label("", "mediaNote", wrap=True)
        column.addWidget(self.detail_info)
        self.episode_head = SectionTitle("Episodes")
        column.addWidget(self.episode_head)
        self.spoiler_note = label("", "mediaNote", wrap=True)
        self.spoiler_note.setVisible(False)
        column.addWidget(self.spoiler_note)
        picker = QHBoxLayout()
        picker.setSpacing(8)
        self.season_label = label("Season", "detailNote")
        picker.addWidget(self.season_label)
        self.season_combo = QComboBox()
        self.season_combo.setObjectName("seasonPicker")
        self.season_combo.setMinimumWidth(220)
        self.season_combo.setCursor(Qt.PointingHandCursor)
        self.season_combo.setVisible(False)
        self.season_combo.currentIndexChanged.connect(self._season_changed)
        picker.addWidget(self.season_combo)
        picker.addStretch(1)
        column.addLayout(picker)
        self.episode_host = QWidget()
        self.episode_column = QVBoxLayout(self.episode_host)
        self.episode_column.setContentsMargins(0, 0, 0, 0)
        self.episode_column.setSpacing(6)
        column.addWidget(self.episode_host)
        column.addStretch(1)

    def _build_calendar(self, column: QVBoxLayout) -> None:
        """The release calendar as a page; Refresh loads the dates."""
        from .qt_calendar import CalendarDialog

        self.calendar_panel = CalendarDialog(
            self.settings, self, autoload=False, embedded=True
        )
        self.calendar_panel._status.setText(
            "Press Refresh to load release dates from AniList and TMDB."
        )
        column.addWidget(self.calendar_panel, 1)

    # navigation ------------------------------------------------------------
    def show_section(self, key: str) -> None:
        # Choosing a section from the media bar while the video plays stops it,
        # so a hidden page never keeps playing sound.
        if (
            getattr(self, "player", None) is not None
            and self.stack.currentWidget() is self.player
        ):
            self.player.stop_playback()
            self._player_return = None
        self._section = key
        self._artwork.clear()
        self._fill_provider_menu(key)
        for name, button in self._nav.items():
            button.setChecked(name == key)
        page = self._pages.get(key)
        if page is not None:
            self.stack.setCurrentWidget(page)
        if key == "list":
            self._render_list()
        elif key == "history":
            self._render_history()
        # Opening Media reuses a fresh cache and only re-checks when it is old.
        if getattr(self, "_health_opened", False):
            self._startup_health()
        self._health_opened = True
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
        self._install_default_home_loaders()
        self.refresh_home()
        self._install_default_block_loaders()
        self.refresh_columns()
        self.refresh_schedule()

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
            self.refresh_home()

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
        for name in ("_search_grid", "_list_grid", "_history_grid"):
            grid = getattr(self, name, None)
            if grid is not None:
                cards.extend(poster.card for poster in grid.cards())
        if self._detail_card is not None:
            cards.append(self._detail_card)
        return cards

    def card(self, key: str) -> Card | None:
        """The loaded card with ``key``, or ``None`` if this page never saw it."""
        return next((card for card in self._all_cards() if card.key == key), None)

    def _load_image(self, url: str, key: str,
                    apply: Callable[[QPixmap | None], None] | None,
                    fallback: str = "", paint_url: str = "") -> None:
        if not url:
            if fallback:
                self._load_image(fallback, key, apply)
            return
        if key in self._pix and apply is not None:
            apply(self._pix[key])
            return

        def work() -> bytes:
            return cached_image_bytes(url, self._cache_root, key, timeout=25.0)

        def failed(_exc: object) -> None:
            # A missing backdrop still tries the poster, so a detail page never
            # shows a bare gradient when any artwork for the title exists.
            if fallback and fallback != url:
                self._load_image(fallback, key, apply)

        def done(data: object) -> None:
            pix = pixmap_from_bytes(data)
            if pix is None:
                # TMDB answers a sized path with WebP; a build without that
                # decoder gets the untouched JPEG original once, then gives up.
                from . import images

                twin = images.portable_image_url(url)
                if twin:
                    self._load_image(twin, f"{key}-portable", apply, fallback,
                                     paint_url=paint_url or url)
                    return
                failed(None)
                return
            self._pix[key] = pix
            if apply is not None:
                apply(pix)
                return
            self._paint_card(paint_url or url, pix)

        run_async(work, done, failed)

    def _poster_widgets(self) -> list[PosterCard]:
        """Every poster currently on screen, from every grid and carousel."""
        posters: list[PosterCard] = []
        for carousel in getattr(self, "_home_carousels", {}).values():
            posters.extend(carousel.posters())
        if hasattr(self, "_pages_bits"):
            for _head, grid, _empty, _note in self._pages_bits.values():
                posters.extend(grid.cards())
        for name in ("_anime_grid", "_search_grid", "_list_grid", "_history_grid"):
            grid = getattr(self, name, None)
            if grid is not None:
                posters.extend(grid.cards())
        return posters

    def _paint_card(self, url: str, pix: QPixmap) -> None:
        """Apply a downloaded poster to every card that shows that image."""
        for poster in self._poster_widgets():
            if poster.card.poster_url == url:
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
            self._anilist_match = None
            self.anilist_btn.setVisible(False)
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
            # The search *is* the lookup: resolve the anime provider's AniList
            # route in the same pass instead of asking for the title a second
            # time in a dialog.
            if self._anime_provider() in streaming.ANILIST_ANIME_PROVIDERS:
                try:
                    found["anilist"] = streaming.anilist_search(query)
                except Exception as exc:  # noqa: BLE001
                    found["errors"].append(f"AniList: {exc}")  # type: ignore[union-attr]
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
            anilist_id = int(data.get("anilist") or 0)  # type: ignore[arg-type]
            if anilist_id:
                self._anilist_match = (query, anilist_id)
                provider = self._anime_provider()
                self.anilist_btn.setText(
                    f"Play “{query}” on {streaming.LABELS.get(provider, provider)} (AniList)"
                )
                self.anilist_btn.setVisible(True)
            else:
                self._anilist_match = None
                self.anilist_btn.setVisible(False)
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
        """My List button on the home hero: always the hero's own title."""
        self._toggle_list_for(getattr(self, "_hero_card", None))

    def _toggle_detail_list(self) -> None:
        """My List button on a details page: the title that page shows."""
        self._toggle_list_for(getattr(self, "_detail_card", None))

    def _toggle_list_for(self, card: Card | None) -> None:
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
        card = self._fullest_card(card)
        self._detail_card = card
        recently_viewed.record(
            recently_viewed.default_path(),
            kind=card.kind,
            key=card.key,
            title=card.title,
            subtitle=card.meta,
            poster_url=card.poster_url,
        )
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
                         self._detail_backdrop,
                         fallback=card.poster_url if card.backdrop_url else "")
        self._detail_trailer = None
        self.trailer_btn.setVisible(False)
        self.trailer_note.setVisible(False)
        self._load_episodes(card)
        self._load_trailer(card)

    def _fullest_card(self, card: Card) -> Card:
        """Fill in artwork a card arrived without (compare rows, history rows)."""
        known = self.card(card.key) if card.key else None
        if known is None or known is card:
            return card
        updates: dict[str, object] = {
            "poster_url": card.poster_url or known.poster_url,
            "backdrop_url": card.backdrop_url or known.backdrop_url,
            "overview": card.overview or known.overview,
            "year": card.year or known.year,
        }
        if card.rating is None:
            updates["rating"] = known.rating
        return replace(card, **updates)

    def _detail_backdrop(self, pix: QPixmap | None) -> None:
        """Show the backdrop on the hero and blur a copy behind the page."""
        self.detail_hero.set_pixmap(pix)
        card = getattr(self, "_detail_card", None)
        if pix is None or card is None:
            self._artwork.clear()
            return
        self._artwork.show_image(card.backdrop_url or card.poster_url, pix.toImage())

    def paintEvent(self, event) -> None:  # noqa: N802
        """Paint the blurred artwork behind the page, then its content."""
        super().paintEvent(event)
        self._artwork.paint()

    def _sync_spoiler_note(self) -> None:
        """Say the mode is on, and where to turn it off, beside the episodes."""
        text = view_prefs.spoiler_note(self.settings)
        self.spoiler_note.setText(text)
        self.spoiler_note.setVisible(bool(text))

    def _load_episodes(self, card: Card) -> None:
        clear_layout(self.episode_column)
        self._sync_spoiler_note()
        client = getattr(self, "_tmdb", None)
        if card.kind != "tv" or card.tmdb_id == 0 or client is None:
            self.episode_head.setVisible(False)
            self.episode_host.setVisible(False)
            self.season_label.setVisible(False)
            self.season_combo.setVisible(False)
            self.spoiler_note.setVisible(False)
            return
        self.episode_head.setVisible(True)
        self.episode_host.setVisible(True)
        self.season_label.setVisible(True)
        self.episode_head.title.setText("Episodes")
        self.episode_head.count.setText("loading…")

        def work() -> object:
            return client.details("tv", card.tmdb_id)

        def done(result: object) -> None:
            seasons = [
                season for season in (getattr(result, "seasons", ()) or ())
                if int(getattr(season, "number", 0) or 0) > 0
            ]
            self.episode_head.count.setText(f"{len(seasons)} seasons")
            self.season_combo.blockSignals(True)
            self.season_combo.clear()
            for season in seasons:
                number = int(getattr(season, "number", 0) or 0)
                self.season_combo.addItem(
                    f"Season {number} · {getattr(season, 'episode_count', 0)} episodes",
                    number,
                )
            self.season_combo.setVisible(bool(seasons))
            if not seasons:
                self.season_combo.blockSignals(False)
                self.episode_column.addWidget(label("No episodes listed.", "mediaNote"))
                return
            self.season_combo.setCurrentIndex(0)
            self.season_combo.blockSignals(False)
            self._show_season(card, int(self.season_combo.itemData(0)))

        run_async(work, done, lambda exc: self._note_error(exc))

    def _season_changed(self, _index: int) -> None:
        card = getattr(self, "_detail_card", None)
        number = self.season_combo.currentData()
        if card is None or number is None:
            return
        self._show_season(card, int(number))

    def _show_season(self, card: Card, number: int) -> None:
        client = getattr(self, "_tmdb", None)
        if client is None:
            return
        clear_layout(self.episode_column)
        self.episode_head.count.setText(f"Season {number} · loading…")

        def work() -> object:
            return client.season(card.tmdb_id, number)

        def done(result: object) -> None:
            clear_layout(self.episode_column)
            episodes = list(getattr(result, "episodes", ()) or ())
            self.episode_head.count.setText(f"Season {number} · {len(episodes)} episodes")
            if not episodes:
                self.episode_column.addWidget(label("No episodes listed.", "mediaNote"))
                return
            for episode in episodes[:40]:
                row = EpisodeRow(
                    int(getattr(episode, "number", 0) or 0),
                    view_prefs.episode_title(
                        self.settings,
                        str(getattr(episode, "title", "Episode") or "Episode"),
                    ),
                    str(getattr(episode, "air_date", "") or ""),
                )
                row.clicked.connect(
                    lambda e=episode, s=number, c=card: self._play_tmdb(c, s, e)
                )
                self.episode_column.addWidget(row)

        run_async(work, done, lambda exc: self._note_error(exc))

    def _load_trailer(self, card: Card) -> None:
        """Ask TMDB for this title's trailer without blocking the page."""
        if card.kind not in ("movie", "tv") or card.tmdb_id <= 0:
            return
        client = getattr(self, "_tmdb", None)
        fetch = getattr(client, "get", None)

        def work() -> object:
            return trailer_data.load_trailer(
                card.kind, card.tmdb_id, settings=self.settings, fetch=fetch
            )

        def done(result: object) -> None:
            if getattr(self, "_detail_card", None) is not card:
                return
            trailer = getattr(result, "trailer", None)
            self._detail_trailer = trailer
            self.trailer_btn.setVisible(trailer is not None)
            self.trailer_note.setVisible(bool(getattr(result, "error", "")))
            if trailer is None:
                return
            self.trailer_btn.setToolTip(str(trailer.get("name") or "Play the trailer"))
            if trailers.autoplay_allowed(self.settings):
                self._start_trailer(autoplay=True, muted=True)

        run_async(work, done, lambda _exc: self.trailer_note.setVisible(True))

    def _open_trailer(self, _checked: bool = False) -> None:
        """The button: play the trailer the page already found."""
        self._start_trailer(autoplay=True, muted=False)

    def _start_trailer(self, *, autoplay: bool, muted: bool) -> None:
        trailer = getattr(self, "_detail_trailer", None)
        if trailer is None:
            return
        url = trailer_data.embed_url(trailer.get("key"), autoplay=autoplay, muted=muted)
        if not url:
            return
        card = getattr(self, "_detail_card", None)
        TrailerDialog(url, getattr(card, "title", "") or "", self).exec()

    def _play_current(self) -> None:
        """Play button on the home hero: the hero's own title."""
        self._play_card(getattr(self, "_hero_card", None))

    def _play_detail(self) -> None:
        """Play button on a details page: the title that page shows."""
        self._play_card(getattr(self, "_detail_card", None))

    def _play_card(self, card: Card | None) -> None:
        if card is None:
            return
        if card.kind == "anime":
            self._play_anime(card)
        else:
            self._play_tmdb(card)

    # embedded player -------------------------------------------------------
    def play_embedded(self, url: str, title: str = "") -> EmbeddedPlayer:
        """Play inside the Media page: same window, no dialog, no flicker."""
        current = self.stack.currentWidget()
        if current is not None and current is not self.player:
            self._player_return = current
        self.player.play(url, title)
        self.stack.setCurrentWidget(self.player)
        self.player.setFocus()
        return self.player

    def _leave_player(self) -> None:
        """Back stops the video and returns to the page that was open."""
        self.player.stop_playback()
        page = (
            self._player_return
            or self._pages.get(self._section)
            or self._pages["home"]
        )
        self._player_return = None
        self.stack.setCurrentWidget(page)

    # provider picker -------------------------------------------------------
    def _fill_provider_menu(self, section: str) -> None:
        """Name kept for the callers: it now updates the one provider button."""
        current = self._anime_provider() if section == "anime" else self._provider()
        button = getattr(self, "provider_button", None)
        if button is not None:
            button.setToolTip(
                "Provider used to play anime · click to change" if section == "anime"
                else "Provider used to play movies and TV · click to change"
            )
        self._update_badge()

    def _choose_provider(self, key: str) -> None:
        """Persist a provider picked from the menu, exactly as the combo did."""
        if self.settings is None or not key:
            return
        if self._section == "anime":
            self.settings.set("anime_streaming_provider", key)
        else:
            self.settings.set("streaming_provider", key)
        # A manual pick wins for the rest of the session.
        health = getattr(self, "health", None)
        if health is not None:
            health.mark_manual()
            self.health_note.setText(provider_health.MANUAL_MESSAGE)
            self._update_badge()

    # provider health -------------------------------------------------------
    def _menu_providers(self) -> tuple[str, ...]:
        """Every provider the menu offers: the same list the old combo had."""
        if self._section == "anime":
            return tuple(streaming.ANIME_PROVIDERS)
        return tuple(streaming.PROVIDERS)

    def _section_providers(self) -> tuple[str, ...]:
        if self._section == "anime":
            return tuple(
                key for key in streaming.ANIME_PROVIDERS
                if key == streaming.ANI_PROVIDER or streaming.has_anime_route(key)
            )
        return streaming.PROVIDERS

    def _playing(self) -> bool:
        player = getattr(self, "player", None)
        return player is not None and self.stack.currentWidget() is player

    def _startup_health(self) -> None:
        """Steady, non-blocking check after launch; a fresh cache is reused."""
        if self.settings is None or not self.health.enabled():
            return
        self.health.start()

    def test_connection(self) -> None:
        """Ask every provider how fast it answers (three attempts each)."""
        self._measuring = True
        self._update_badge()
        self.health_note.setText("Testing providers…")
        if not self.health.start(force=True):
            self.health_note.setText("A health check is already running.")

    def refresh_health(self) -> None:
        self.test_connection()

    def _apply_health(self, results: object) -> None:
        self._measuring = False
        items = [item for item in (results or []) if isinstance(item, dict)]
        self.health_results = provider_health.sort_results(items)
        self._render_health_rows()
        self._update_badge()
        self._auto_select()

    def _render_health_rows(self) -> None:
        while self.health_rows.count():
            item = self.health_rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for entry in self.health_results:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(14)
            name = label(streaming.LABELS.get(entry["provider"], entry["provider"]))
            name.setFixedWidth(120)
            ms = label(
                f"{entry['ms']} ms" if entry.get("ms") is not None else "--"
            )
            ms.setFixedWidth(80)
            status = label(str(entry.get("status") or ""), "mediaNote")
            reason = entry.get("reason") or ""
            if reason:
                status.setText(f"{status.text()} · {reason}")
            for widget in (name, ms, status):
                if not entry.get("ok"):
                    widget.setStyleSheet("color: #8a8f98;")
            line.addWidget(name)
            line.addWidget(ms)
            line.addWidget(status, 1)
            self.health_rows.addWidget(row)
        self.health_list.setVisible(getattr(self, "_health_list_open", False))
        if self.health_results:
            reachable = sum(1 for entry in self.health_results if entry.get("ok"))
            self.health_note.setText(
                f"{reachable} of {len(self.health_results)} providers reachable"
            )

    def _active_provider(self) -> str:
        return self._anime_provider() if self._section == "anime" else self._provider()

    def _provider_row_text(self, key: str) -> str:
        """``Name  ·  180 ms`` / ``offline`` / ``not tested`` for one menu row."""
        name = streaming.LABELS.get(key, key)
        entry = next(
            (item for item in getattr(self, "health_results", [])
             if item.get("provider") == key),
            None,
        )
        if entry is None:
            return f"{name}  ·  not tested"
        if not entry.get("ok"):
            return f"{name}  ·  offline"
        ms = entry.get("ms")
        return f"{name}  ·  {ms} ms" if isinstance(ms, int) else f"{name}  ·  not tested"

    def _provider_menu(self) -> object:
        """Auto, one row per provider of this section, then Test connections."""
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        fastest = (
            provider_health.fastest(self.health_results, self._section_providers())
            if self.health_results else None
        )
        auto_text = "Auto (fastest)"
        if fastest:
            auto_text = f"Auto (fastest: {streaming.LABELS.get(fastest, fastest)})"
        auto = menu.addAction(auto_text)
        auto.setCheckable(True)
        auto.setChecked(self.health.enabled())
        auto.triggered.connect(lambda _checked=False: self._toggle_auto())
        menu.addSeparator()
        current = self._active_provider()
        for key in self._menu_providers():
            action = menu.addAction(self._provider_row_text(key))
            action.setCheckable(True)
            action.setChecked(key == current)
            action.triggered.connect(lambda _checked=False, name=key: self._choose_provider(name))
        menu.addSeparator()
        test = menu.addAction("Test connections now")
        test.triggered.connect(self.test_connection)
        return menu

    def _toggle_auto(self) -> None:
        if self.settings is None:
            return
        self.settings.set(provider_health.AUTO_KEY, not self.health.enabled())
        health = getattr(self, "health", None)
        if health is not None:
            health.mark_manual() if not health.enabled() else None
        self._update_badge()

    def _show_provider_menu(self) -> None:
        menu = self._provider_menu()
        menu.exec(self.provider_button.mapToGlobal(self.provider_button.rect().bottomLeft()))

    _show_badge_menu = _show_provider_menu

    def _toggle_health_list(self) -> None:
        self._health_list_open = not getattr(self, "_health_list_open", False)
        self.health_list.setVisible(self._health_list_open and bool(self.health_results))

    def _update_badge(self) -> None:
        key = self._active_provider()
        name = streaming.LABELS.get(key, key)
        entry = next(
            (item for item in getattr(self, "health_results", [])
             if item.get("provider") == key),
            None,
        )
        button = getattr(self, "provider_button", None)
        if button is None:
            return
        if getattr(self, "_measuring", False) and entry is None:
            button.set_state(name, "pending")
        elif entry is None:
            button.set_state(name, "unknown")
        elif not entry.get("ok"):
            button.set_state(name, "offline")
        else:
            button.set_state(name, "ok", entry.get("ms"))

    def _auto_select(self) -> None:
        """Apply the fastest provider when automatic selection allows it."""
        if not self.health_results:
            return
        if not provider_health.should_auto_apply(
            enabled=self.health.enabled(), playing=self._playing(),
            manual=self.health.manual,
        ):
            return
        key = provider_health.fastest(self.health_results, self._section_providers())
        if key is None:
            self.health_note.setText(provider_health.NO_PROVIDER_MESSAGE)
            return
        if key != self._active_provider():
            self._select_provider(key)

    def _select_provider(self, key: str) -> None:
        if self.settings is not None:
            field_name = (
                "anime_streaming_provider" if self._section == "anime"
                else "streaming_provider"
            )
            self.settings.set(field_name, key)
        self._fill_provider_menu(self._section)
        self._update_badge()

    def use_fastest(self) -> None:
        """Pick the quickest reachable provider for this section; no playback."""
        results = self.health_results
        if not results:
            self.health_note.setText(
                "Test the providers first, then Use fastest can pick one."
            )
            return
        key = provider_health.fastest(results, self._section_providers())
        if key is None:
            self.health_note.setText(provider_health.NO_PROVIDER_MESSAGE)
            return
        self._select_provider(key)
        self.health_note.setText(
            f"Using {streaming.LABELS.get(key, key)} for "
            f"{'anime' if self._section == 'anime' else 'movies and TV'}."
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.health.shutdown()
        super().closeEvent(event)

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
        self.play_embedded(url, card.title)

    def _play_anilist_match(self) -> None:
        """Play the AniList match the search already resolved.

        The AniList routes need an id that TMDB and MyAnimeList do not provide
        for every title, so the search resolves it for the term that was typed
        and offers it as one button, instead of a second lookup box.
        """
        match = getattr(self, "_anilist_match", None)
        if match is None:
            return
        title, anilist_id = match
        provider = self._anime_provider()
        if provider not in streaming.ANILIST_ANIME_PROVIDERS:
            self._note_error(
                "Pick ToFlix or CineLite as the anime provider first; those two "
                "routes take an AniList id."
            )
            return
        try:
            url = streaming.anime_url(provider, anilist_id, 1)
        except (streaming.EmbedUrlError, ValueError) as exc:
            self._note_error(str(exc))
            return
        self.play_embedded(url, title)

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
            elif card.tmdb_id:
                # A TMDB provider plays anime too; it needs the TMDB id that a
                # MAL-keyed title may not have, so ask TMDB for one.
                self._play_tmdb(card)
                return
            else:
                self._note_error(
                    f"{streaming.LABELS.get(provider, provider)} has no anime "
                    "route for this title. Pick ToFlix, CineLite or Vidy, or "
                    "ani.pm, in the provider menu."
                )
                return
        except (streaming.EmbedUrlError, ValueError) as exc:
            self._note_error(str(exc))
            return
        self._record_history(card)
        self.play_embedded(url, card.title)

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
        # Only a details page links here, so the page's own card is the answer;
        # falling back to the home hero used to open the trending title instead.
        card = getattr(self, "_detail_card", None)
        if card is not None and card.tmdb_id:
            platform_support.open_url(f"https://www.themoviedb.org/{card.kind}/{card.tmdb_id}")


class ProviderButton(QPushButton):
    """Current provider and its ping, with a painted dot; click opens the menu."""

    COLOURS = {"ok": "#4caf72", "slow": "#e0a458", "offline": "#8a8f98",
               "unknown": "#8a8f98", "pending": "#e0a458"}
    #: Painted dot, the gap after it, and the right padding.
    DOT_LEFT = 11
    DOT_SIZE = 6
    GAP = 7
    PAD = 8
    #: Every suffix a connection test can put on screen.  ``sizeHint`` keeps
    #: room for the widest of them, so the header does not resize mid-test.
    STATE_SUFFIXES = (" · not tested", ": offline", " · …", " · 0000 ms")
    #: Sub-pixel slack between measuring a width and painting into it.
    SLACK = 1
    #: The name is never squeezed below this, even in a tight header.
    MIN_NAME_ROOM = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mediaBadge")
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Provider · click to change or test connections")
        self._name = ""
        self._suffix = ""
        self._tone = "unknown"
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_state(self, name: str, status: str = "unknown", ms: object = None) -> None:
        self._name = str(name or "Provider")
        if status == "ok":
            number = int(ms or 0)
            self._suffix = f" · {number} ms"
            self._tone = "ok" if number < 300 else ("slow" if number <= 800 else "offline")
        elif status == "offline":
            self._suffix = ": offline"
            self._tone = "offline"
        elif status == "pending":
            self._suffix = " · …"
            self._tone = "pending"
        else:
            self._suffix = " · not tested"
            self._tone = "unknown"
        super().setText(self._full_text())
        self.updateGeometry()

    def _full_text(self) -> str:
        return f"{self._name}{self._suffix}"

    def _metrics(self) -> QFontMetricsF:
        """The font ``paintEvent`` draws with.

        The theme sets the family and size through the application stylesheet,
        so the widget only holds its final font after it has been polished.
        """
        self.ensurePolished()
        return QFontMetricsF(self.font())

    def _text_left(self) -> int:
        return self.DOT_LEFT + self.DOT_SIZE + self.GAP

    def _states(self) -> tuple[str, ...]:
        """The suffix on screen, plus every one a connection test can swap in."""
        return tuple(dict.fromkeys((self._suffix, *self.STATE_SUFFIXES)))

    def _name_room(self, width: int) -> int:
        """Pixels left for the name in a button ``width`` wide.

        ``paintEvent`` elides the name to exactly this, so measuring and
        painting cannot drift apart.
        """
        metrics = self._metrics()
        used = self._text_left() + ceil(metrics.horizontalAdvance(self._suffix)) + self.PAD
        return max(self.MIN_NAME_ROOM, int(width) - used)

    def _needed_width(self, suffix: str) -> int:
        """Width for the dot, the whole name, this suffix and the padding."""
        metrics = self._metrics()
        total = max(
            metrics.horizontalAdvance(self._name) + metrics.horizontalAdvance(suffix),
            metrics.horizontalAdvance(self._name + suffix),
        )
        return self._text_left() + ceil(total) + self.PAD + self.SLACK

    def _ping_width(self) -> int:
        """The dot and the ping: the name is elided, this never is."""
        return (
            self._text_left() + ceil(self._metrics().horizontalAdvance(self._suffix))
            + self.PAD
        )

    def _content_width(self) -> int:
        """Room for the whole name in the widest state, so a ping cannot resize it."""
        return max(self._needed_width(suffix) for suffix in self._states())

    def _drawn_name(self, width: int) -> str:
        """The name as painted in a button ``width`` wide, elided if it must be."""
        return self._metrics().elidedText(self._name, Qt.ElideRight, self._name_room(width))

    def displayed_text(self) -> str:
        """Exactly the string ``paintEvent`` draws, elided name included."""
        return self._drawn_name(self.width()) + self._suffix

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        """Ask for the full text in any state; the header only elides on squeeze."""
        hint = super().sizeHint()
        return QSize(max(self._ping_width(), self._content_width()), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._ping_width(), super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.setBrush(QColor(255, 255, 255, 8) if self.underMouse() else Qt.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        dot = QRectF(self.DOT_LEFT, self.height() / 2 - self.DOT_SIZE / 2,
                     self.DOT_SIZE, self.DOT_SIZE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.COLOURS.get(self._tone, "#8a8f98")))
        painter.drawEllipse(dot)
        painter.setFont(self.font())
        text_left = self._text_left()
        painter.setPen(QColor(self.palette().color(self.foregroundRole())))
        painter.drawText(
            QRectF(text_left, 0, self.width() - text_left - self.PAD, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter, self.displayed_text(),
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


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
    "EmbeddedPlayer",
    "MediaPage",
    "PosterCard",
    "PosterGrid",
    "card_from_anime",
    "card_from_history",
    "card_from_tmdb",
    "rating_text",
]
