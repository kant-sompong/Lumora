"""The Qt release calendar: what airs or releases on which day.

Same data and wording as the GTK window (:mod:`lgl.gtk_calendar`): entries from
:mod:`lgl.release_feed`, grouping and formatting from :mod:`lgl.anime_calendar`.
Fetching runs through :func:`run_async` so the dialog never freezes.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import anime_calendar as cal
from . import release_feed
from .qt_widgets import run_async

TZ = "local"


class CalendarDialog(QDialog):
    def __init__(self, settings: Any, parent: QWidget | None = None, *, days: int = 14, autoload: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Release calendar")
        self.resize(720, 640)
        self.settings = settings
        self._days = days
        self._result = release_feed.FeedResult()
        self._busy = False

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self._filter = QComboBox()
        for key, label in release_feed.FILTERS:
            self._filter.addItem(label, key)
        self._filter.currentIndexChanged.connect(lambda _i: self._render())
        bar.addWidget(self._filter)
        bar.addStretch(1)
        self._refresh = QPushButton("Refresh")
        self._refresh.clicked.connect(lambda: self.reload(force=True))
        bar.addWidget(self._refresh)
        layout.addLayout(bar)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("detail-note")
        layout.addWidget(self._status)

        self._body = QWidget()
        self._rows = QVBoxLayout(self._body)
        self._rows.setContentsMargins(0, 0, 8, 0)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setWidget(self._body)
        layout.addWidget(scroller, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(close)
        layout.addLayout(foot)
        if autoload:
            self.reload()

    # -- data -----------------------------------------------------------
    def reload(self, force: bool = False) -> None:
        if self._busy:
            return
        self._busy = True
        self._refresh.setEnabled(False)
        self._status.setText("Loading release dates…")
        getter = release_feed.tmdb_getter(self.settings)
        run_async(
            lambda: release_feed.load_feed(
                release_feed.default_cache_path(), days=self._days, force=force, tmdb_get=getter
            ),
            self._loaded,
            lambda error: self._loaded(release_feed.FeedResult(errors=[f"Could not load the calendar: {error}"])),
        )

    def _loaded(self, result: object) -> None:
        self._busy = False
        self._refresh.setEnabled(True)
        if isinstance(result, release_feed.FeedResult):
            self._result = result
        self._render()
        release_feed.notify_due(self.settings, self._result.entries, notify=_announce)

    def set_result(self, result: release_feed.FeedResult) -> None:
        """Show ``result`` without fetching (used by tests and screenshots)."""
        self._loaded(result)

    # -- drawing --------------------------------------------------------
    def _render(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        followed = cal.follows(self.settings)
        choice = self._filter.currentData() or "all"
        entries = release_feed.select(self._result.entries, choice, followed)
        for heading, group in cal.grouped(entries, TZ):
            title = QLabel(heading)
            title.setObjectName("filter-head")
            title.setStyleSheet("font-weight: 700; margin-top: 10px;")
            self._rows.addWidget(title)
            for entry in group:
                self._rows.addWidget(self._row(entry, entry.key in followed))
        self._rows.addStretch(1)

        notes = list(self._result.errors)
        if self._result.from_cache:
            notes.insert(0, "Showing saved dates (could not reach the servers).")
        if not entries:
            notes.append("Nothing in this view." if self._result.entries else "No release dates yet.")
        self._status.setText(" ".join(notes))

    def _row(self, entry: cal.Entry, following: bool) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 2, 0, 2)
        name = QLabel(entry.title + (f" — {entry.episode}" if entry.episode else ""))
        name.setWordWrap(True)
        line.addWidget(name, 1)
        kind = {"anime": "Anime", "tv": "TV", "movie": "Movie"}[release_feed.kind_of(entry)]
        when = QLabel(f"{kind} · {cal.format_when(entry, TZ)}")
        when.setObjectName("detail-note")
        line.addWidget(when)
        button = QPushButton("Following" if following else "Follow")
        button.setCheckable(True)
        button.setChecked(following)
        button.toggled.connect(lambda on, key=entry.key, b=button: self._on_follow(b, key, on))
        line.addWidget(button)
        return row

    def _on_follow(self, button: QPushButton, key: str, on: bool) -> None:
        if on:
            if not cal.follow(self.settings, key):
                button.setChecked(False)
                self._status.setText(f"You can follow up to {cal.MAX_FOLLOWS} titles.")
                return
            button.setText("Following")
        else:
            cal.unfollow(self.settings, key)
            button.setText("Follow")


def _announce(settings: Any, title: str) -> bool:
    from . import notifications

    return notifications.episode_released(settings, title)


def show_calendar(settings: Any, parent: QWidget | None = None) -> CalendarDialog:
    dialog = CalendarDialog(settings, parent)
    dialog.exec()
    return dialog


__all__ = ["CalendarDialog", "show_calendar"]
