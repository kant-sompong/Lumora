"""Qt screens for finding something to watch: recommendations, hidden gems,
similar titles, collection gaps, favourites, an activity timeline and a season
comparison. The ranking lives in :mod:`lgl.discovery`, :mod:`lgl.stats` and
:mod:`lgl.season_compare`; these dialogs only present it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import discovery, season_compare, stats


def _row(item: Mapping[str, Any]) -> str:
    """One readable line for a title, with its genres when they are known."""
    title = str(item.get("title") or item.get("key") or "Untitled").strip()
    genres = item.get("genres") or ()
    if isinstance(genres, str):
        genres = [part for part in genres.replace(";", ",").split(",")]
    labels = " · ".join(str(part).strip() for part in genres if str(part).strip())
    year = str(item.get("year") or "").strip()
    details = " · ".join(part for part in (year, labels) if part)
    return f"{title}\n{details}" if details else title


def _fill(widget: QListWidget, items: Sequence[Mapping[str, Any]]) -> None:
    widget.clear()
    for item in items:
        QListWidgetItem(_row(item), widget)


def _empty_note(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("detailNote")
    label.setWordWrap(True)
    return label


class DiscoverDialog(QDialog):
    """Four ways to find the next title, all from the loaded collection."""

    def __init__(
        self,
        items: Sequence[Mapping[str, Any]],
        liked: Sequence[str] = (),
        owned: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discover")
        self.resize(600, 480)
        self.items = [dict(item) for item in items]
        self.liked = [str(key) for key in liked]
        self.owned = [str(key) for key in owned]

        layout = QVBoxLayout(self)
        heading = QLabel("DISCOVER", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)

        # -- For you --------------------------------------------------
        self.recommendations = QListWidget(self)
        _fill(self.recommendations, discovery.recommend(self.items, self.liked))
        self.tabs.addTab(
            self._page(self.recommendations, "Like what you already have; nothing you own is repeated."),
            "For you",
        )

        # -- Hidden gems ----------------------------------------------
        self.gems = QListWidget(self)
        _fill(self.gems, discovery.hidden_gems(self.items))
        self.tabs.addTab(
            self._page(self.gems, "Well rated but not widely known."), "Hidden gems"
        )

        # -- More like this -------------------------------------------
        self.seed = QComboBox(self)
        for item in self.items:
            self.seed.addItem(str(item.get("title") or item.get("key") or "Untitled"), item.get("key"))
        self.seed.currentIndexChanged.connect(self.refresh_similar)
        self.similar_list = QListWidget(self)
        similar_page = QWidget(self)
        similar_layout = QVBoxLayout(similar_page)
        similar_layout.addWidget(self.seed)
        similar_layout.addWidget(self.similar_list, 1)
        similar_layout.addWidget(_empty_note("Shares genres or tags with the title above.", self))
        self.tabs.addTab(similar_page, "More like this")
        self.refresh_similar()

        # -- Complete the set -----------------------------------------
        self.suggestions = QListWidget(self)
        _fill(self.suggestions, discovery.suggestions(self.items, self.owned))
        self.tabs.addTab(
            self._page(self.suggestions, "Fills genres your collection is still missing."),
            "Complete the set",
        )

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _page(self, widget: QListWidget, note: str) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(widget, 1)
        layout.addWidget(_empty_note(note, page))
        return page

    def refresh_similar(self) -> None:
        key = str(self.seed.currentData() or "")
        _fill(self.similar_list, discovery.similar(self.items, key))


class FavouritesDialog(QDialog):
    """Favourite games beside favourite movies, series and anime."""

    def __init__(
        self,
        entries: Sequence[Mapping[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favourites")
        self.resize(620, 460)
        buckets = stats.split(entries)
        self.games = list(buckets.get("game", []))
        self.media = [
            row
            for kind, rows in buckets.items()
            if kind not in ("game", "other")
            for row in rows
        ]

        layout = QVBoxLayout(self)
        heading = QLabel("FAVOURITES", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        columns = QHBoxLayout()
        columns.addLayout(self._column("Games", self.games), 1)
        columns.addLayout(self._column("Movies, series and anime", self.media), 1)
        layout.addLayout(columns, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _column(self, name: str, rows: Sequence[Mapping[str, Any]]) -> QVBoxLayout:
        layout = QVBoxLayout()
        label = QLabel(f"{name} ({len(rows)})", self)
        layout.addWidget(label)
        widget = QListWidget(self)
        _fill(widget, rows)
        if name == "Games":
            self.game_list = widget
        else:
            self.media_list = widget
        layout.addWidget(widget, 1)
        return layout


class ActivityTimelineDialog(QDialog):
    """What was added, played or opened, newest first."""

    def __init__(
        self,
        events: Sequence[Mapping[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activity")
        self.resize(520, 460)
        self.rows = stats.timeline(events, limit=60)

        layout = QVBoxLayout(self)
        heading = QLabel("ACTIVITY", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        self.list = QListWidget(self)
        for day, label in self.rows:
            QListWidgetItem(f"{day}  ·  {label}", self.list)
        layout.addWidget(self.list, 1)

        self.empty = QLabel("Nothing recorded yet.", self)
        self.empty.setObjectName("detailNote")
        self.empty.setVisible(not self.rows)
        layout.addWidget(self.empty)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)


class SeasonCompareDialog(QDialog):
    """What two anime seasons have in common and what each has alone."""

    def __init__(
        self,
        items: Sequence[Mapping[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compare seasons")
        self.resize(560, 480)
        self.items = [dict(item) for item in items]
        self.seasons = season_compare.seasons(self.items)

        layout = QVBoxLayout(self)
        heading = QLabel("COMPARE SEASONS", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        pickers = QHBoxLayout()
        self.first = QComboBox(self)
        self.second = QComboBox(self)
        for name in self.seasons:
            self.first.addItem(name)
            self.second.addItem(name)
        if len(self.seasons) > 1:
            self.second.setCurrentIndex(1)
        self.first.currentIndexChanged.connect(self.refresh)
        self.second.currentIndexChanged.connect(self.refresh)
        pickers.addWidget(self.first, 1)
        pickers.addWidget(QLabel("vs", self))
        pickers.addWidget(self.second, 1)
        layout.addLayout(pickers)

        self.tabs = QTabWidget(self)
        self.shared = QListWidget(self)
        self.only_first = QListWidget(self)
        self.only_second = QListWidget(self)
        self.tabs.addTab(self.shared, "In both")
        self.tabs.addTab(self.only_first, "Only first")
        self.tabs.addTab(self.only_second, "Only second")
        layout.addWidget(self.tabs, 1)

        self.summary = QLabel("", self)
        self.summary.setObjectName("detailNote")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        first = str(self.first.currentText() or "")
        second = str(self.second.currentText() or "")
        result = season_compare.compare(self.items, first, second)
        _fill(self.shared, result["shared"])
        _fill(self.only_first, result["only_first"])
        _fill(self.only_second, result["only_second"])
        self.tabs.setTabText(0, f"In both ({len(result['shared'])})")
        self.tabs.setTabText(1, f"Only {first or 'first'} ({len(result['only_first'])})")
        self.tabs.setTabText(2, f"Only {second or 'second'} ({len(result['only_second'])})")
        self.summary.setText(season_compare.summary(result))


__all__ = [
    "ActivityTimelineDialog",
    "DiscoverDialog",
    "FavouritesDialog",
    "SeasonCompareDialog",
]
