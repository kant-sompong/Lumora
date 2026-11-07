"""The Qt page for anime hidden gems: thresholds, a list and Refresh.

The ranking is :mod:`lgl.hidden_gems`; this page only draws it.  It loads when
the tab is first opened or when Refresh is pressed, on the shared worker pool
(``run_async``) so the window never freezes.  Tests pass ``threaded=False`` to
stay synchronous.

Only AniList anime is covered — games and TMDB titles have no score/popularity
pair here — and the page says so.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import hidden_gems, season_data
from .qt_widgets import run_async

#: Where the two thresholds live in the app settings.
MIN_SCORE_KEY = "hidden_gems_min_score"
MAX_POPULARITY_KEY = "hidden_gems_max_popularity"

HINT = "Select a title to see its score and popularity."
BLURB = (
    "AniList anime only: games and TMDB titles have no score and popularity "
    "pair here, so they are not covered."
)


class HiddenGemsPanel(QWidget):
    """Score and popularity thresholds over AniList's anime, best rated first."""

    def __init__(
        self,
        settings: Any = None,
        *,
        post: hidden_gems.Post | None = None,
        cache_path: Any = None,
        threaded: bool = True,
        min_score: int = hidden_gems.MIN_SCORE,
        max_popularity: int = 0,
        excluded: Any = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._post = post
        self._cache_path = cache_path
        self._threaded = threaded
        self.excluded = [str(value) for value in excluded if str(value).strip()]
        self.result: hidden_gems.GemsResult | None = None
        self.loaded = False
        self._busy = False

        layout = QVBoxLayout(self)
        heading = QLabel("HIDDEN GEMS (ANIME)", self)
        heading.setObjectName("detailEyebrow")
        layout.addWidget(heading)

        blurb = QLabel(BLURB, self)
        blurb.setObjectName("detailNote")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Score at least", self))
        self.score = QSpinBox(self)
        self.score.setRange(0, 100)
        self.score.setMinimumWidth(90)
        self.score.setValue(self._saved_score(min_score))
        controls.addWidget(self.score)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Popularity below", self))
        self.popularity = QSpinBox(self)
        self.popularity.setRange(0, hidden_gems.MAX_POPULARITY)
        self.popularity.setSingleStep(100)
        self.popularity.setMinimumWidth(130)
        self.popularity.setValue(self._saved_popularity(max_popularity))
        self.popularity.setToolTip("0 uses the median popularity of the fetched titles.")
        controls.addWidget(self.popularity)
        controls.addSpacing(16)
        self.refresh_btn = QPushButton("Refresh", self)
        self.refresh_btn.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.cutoff_note = QLabel(
            "Popularity 0 means the median of the fetched titles.", self
        )
        self.cutoff_note.setObjectName("detailNote")
        self.cutoff_note.setWordWrap(True)
        layout.addWidget(self.cutoff_note)

        # A failure has to be readable, not another line of faint helper text.
        self.error = QLabel("", self)
        self.error.setStyleSheet("color: #e0a458;")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        layout.addWidget(self.error)

        self.status = QLabel(
            "Open this tab or press Refresh to load anime from AniList.", self
        )
        self.status.setObjectName("detailNote")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.list = QListWidget(self)
        self.list.currentItemChanged.connect(self._on_selected)
        layout.addWidget(self.list, 1)

        self.detail = QLabel(HINT, self)
        self.detail.setObjectName("detailNote")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

    # -- saved thresholds ------------------------------------------------
    def _saved_score(self, fallback: int) -> int:
        saved = self.settings.get(MIN_SCORE_KEY) if self.settings is not None else None
        return hidden_gems.clean_score(fallback if saved is None else saved)

    def _saved_popularity(self, fallback: int) -> int:
        saved = (
            self.settings.get(MAX_POPULARITY_KEY) if self.settings is not None else None
        )
        return hidden_gems.clean_popularity(fallback if saved is None else saved)

    def _remember(self, score: int, popularity: int) -> None:
        """Save both controls as they are; 0 stays 0 rather than the median."""
        if self.settings is None:
            return
        self.settings.update(**{MIN_SCORE_KEY: score, MAX_POPULARITY_KEY: popularity})

    # -- thresholds -----------------------------------------------------
    def thresholds(self) -> tuple[int, int]:
        """The two numbers in the controls, cleaned to what the module expects."""
        return (
            hidden_gems.clean_score(self.score.value()),
            hidden_gems.clean_popularity(self.popularity.value()),
        )

    # -- loading --------------------------------------------------------
    def ensure_loaded(self) -> None:
        """Load once, the first time the tab is opened."""
        if not self.loaded and not self._busy:
            self.refresh()

    def refresh(self) -> None:
        """Fetch or filter the catalogue for the current thresholds."""
        if self._busy:
            return
        self._busy = True
        self.loaded = True
        self.refresh_btn.setEnabled(False)
        self.status.setText("Loading anime from AniList…")
        self.error.setText("")
        self.error.setVisible(False)
        score, popularity = self.thresholds()
        self._remember(score, popularity)
        excluded = list(self.excluded)

        def load() -> hidden_gems.GemsResult:
            return hidden_gems.load_gems(
                post=self._post,
                cache_path=self._cache_path,
                min_score=score,
                max_popularity=popularity,
                excluded=excluded,
            )

        if self._threaded:
            run_async(load, self.show_result, self.show_failure)
        else:
            self.show_result(load())

    def show_result(self, result: hidden_gems.GemsResult) -> None:
        """Draw ``result`` without loading anything (tests and screenshots)."""
        self._busy = False
        self.result = result
        self.refresh_btn.setEnabled(True)
        self.status.setText(result.note())
        self.error.setText(result.error_text)
        self.error.setVisible(bool(result.error_text))
        if result.cutoff:
            source = (
                "from the control" if result.max_popularity
                else "the median of the fetched titles"
            )
            self.cutoff_note.setText(f"Popularity below {result.cutoff:,} ({source}).")

        self.list.clear()
        for entry in result.entries:
            item = QListWidgetItem(hidden_gems.entry_line(entry, result.scanned))
            item.setData(Qt.UserRole, entry)
            item.setToolTip(str(entry.get("title") or ""))
            self.list.addItem(item)
        self.detail.setText(HINT)

    def show_failure(self, exc: object) -> None:
        """The worker itself failed; describe it in one line."""
        self._busy = False
        self.refresh_btn.setEnabled(True)
        self.status.setText("")
        self.error.setText(f"Could not load: {season_data.reason_of(exc)}")
        self.error.setVisible(True)

    # -- behaviour ------------------------------------------------------
    def _on_selected(self, current: QListWidgetItem | None, _previous: Any = None) -> None:
        entry = current.data(Qt.UserRole) if current is not None else None
        scanned = self.result.scanned if self.result is not None else 0
        self.detail.setText(hidden_gems.entry_details(entry, scanned) or HINT)


__all__ = ["BLURB", "HINT", "HiddenGemsPanel"]
