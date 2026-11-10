"""Toolkit-independent state for the streaming-style feed widgets.

The Qt widgets in :mod:`lgl.qt_feed` and the GTK widgets in
:mod:`lgl.gtk_feed` draw the same three pieces — a poster row, a three-column
list and a seven-day schedule — so the parts that are not drawing live here:

* how a poster card's title and subtitle are read,
* what a poster row is showing (nothing, a loading note, one error line, or
  cards) and how many cards fit a given width,
* which rows each column of the three-column list shows, and whether it is
  showing all of its items or only the first few,
* which day the schedule strip has selected and which airings fall on it.

Nothing here imports a GUI toolkit, so the same behaviour can be tested on a
machine with no display at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

#: Shown in place of a missing subtitle, exactly as the Qt posters do.
BLANK_META = "—"
UNTITLED = "Untitled"
LOADING_TEXT = "Loading…"
ERROR_PREFIX = "Could not load: "
DAYS_IN_WEEK = 7


def field(card: Any, name: str) -> str:
    """One text field from a poster card or from a plain row mapping."""
    if isinstance(card, Mapping):
        return str(card.get(name) or "")
    return str(getattr(card, name, "") or "")


def poster_text(card: Any) -> tuple[str, str]:
    """The title and subtitle a plain poster shows for *card*."""
    title = field(card, "title") or UNTITLED
    return title, field(card, "meta") or BLANK_META


def error_line(reason: object) -> str:
    """The one dim line a widget shows instead of its content."""
    text = str(reason or "").strip()
    return f"{ERROR_PREFIX}{text}" if text else ""


# -- poster row -------------------------------------------------------------
IDLE = "idle"
LOADING = "loading"
LOADED = "loaded"
ERROR = "error"
HIDDEN = "hidden"


@dataclass
class PosterRowState:
    """What a poster row should be showing right now."""

    #: Card width and the gap between cards, used by :meth:`fit_count`.
    card_width: int = 150
    spacing: int = 16
    status: str = IDLE
    placeholder: str = LOADING_TEXT
    error: str = ""
    cards: list[Any] = dataclass_field(default_factory=list)

    # states ----------------------------------------------------------------
    def set_loading(self, text: str = LOADING_TEXT) -> None:
        self.status = LOADING
        self.placeholder = text or LOADING_TEXT
        self.error = ""

    def set_cards(self, cards: Sequence[Any]) -> None:
        """Fill the row; an empty list means the section hides itself."""
        self.cards = list(cards)
        self.error = ""
        self.status = LOADED if self.cards else HIDDEN

    def set_error(self, reason: object) -> None:
        """One dim line instead of the row; an empty reason hides the row."""
        line = error_line(reason)
        self.cards = []
        if not line:
            self.error = ""
            self.status = HIDDEN
            return
        self.error = line
        self.status = ERROR

    # questions -------------------------------------------------------------
    @property
    def visible(self) -> bool:
        """A row that has never loaded and a row with no cards both hide."""
        return self.status not in (IDLE, HIDDEN)

    @property
    def showing_cards(self) -> bool:
        return self.status == LOADED and bool(self.cards)

    @property
    def count(self) -> int:
        return len(self.cards)

    def titles(self) -> list[str]:
        return [field(card, "title") for card in self.cards]

    def fit_count(self, available_width: int, card_width: int | None = None,
                  spacing: int | None = None) -> int:
        """How many cards fit a strip *available_width* pixels wide.

        Cards and gaps alternate, so a strip holds one more card than it holds
        gaps: ``n * card + (n - 1) * gap <= width``. Anything narrower than one
        card still shows one card, and the count never exceeds the cards the
        row actually has.
        """
        card = int(card_width if card_width is not None else self.card_width)
        gap = int(spacing if spacing is not None else self.spacing)
        width = int(available_width)
        if card <= 0:
            return 0
        if width < card:
            fits = 1
        else:
            fits = (width + gap) // (card + gap)
        fits = max(1, fits)
        return min(fits, len(self.cards)) if self.cards else fits


# -- three columns ----------------------------------------------------------
COLUMN_KEYS = ("new_release", "new_added", "just_completed")
COLUMN_HEADINGS = (
    ("new_release", "New Release"),
    ("new_added", "New Added"),
    ("just_completed", "Just Completed"),
)
#: Rows a column shows before "Show all" is offered.
COLUMN_LIMIT = 5
SHOW_ALL = "Show all"
SHOW_FEWER = "Show fewer"


class ThreeColumnState:
    """New Release / New Added / Just Completed, five rows each until expanded."""

    KEYS = COLUMN_KEYS
    HEADINGS = COLUMN_HEADINGS
    LIMIT = COLUMN_LIMIT

    def __init__(self, *, limit: int = COLUMN_LIMIT) -> None:
        self.limit = max(1, int(limit))
        self.items: dict[str, list[Any]] = {key: [] for key in self.KEYS}
        self.errors: dict[str, str] = {key: "" for key in self.KEYS}
        self.expanded: dict[str, bool] = {key: False for key in self.KEYS}

    # data ------------------------------------------------------------------
    def set_items(self, key: str, items: Iterable[Any]) -> None:
        if key not in self.items:
            return
        self.items[key] = list(items)
        self.errors[key] = ""
        if len(self.items[key]) <= self.limit:
            self.expanded[key] = False

    def set_error(self, key: str, reason: object) -> None:
        """Hide this column's rows and show one dim ``Could not load:`` line."""
        if key not in self.errors:
            return
        self.errors[key] = error_line(reason)
        self.items[key] = []
        self.expanded[key] = False

    def set_columns(
        self,
        *,
        new_release: Iterable[Any] | None = None,
        new_added: Iterable[Any] | None = None,
        just_completed: Iterable[Any] | None = None,
    ) -> None:
        for key, items in (
            ("new_release", new_release),
            ("new_added", new_added),
            ("just_completed", just_completed),
        ):
            self.set_items(key, items or [])

    # questions -------------------------------------------------------------
    def visible(self, key: str) -> bool:
        """A column shows when it has rows or an error line."""
        return bool(self.items.get(key)) or bool(self.errors.get(key))

    def any_visible(self) -> bool:
        return any(self.visible(key) for key in self.KEYS)

    def can_expand(self, key: str) -> bool:
        return len(self.items.get(key, [])) > self.limit

    def rows(self, key: str) -> list[Any]:
        """The items this column shows: the first ``limit``, or all of them."""
        items = self.items.get(key, [])
        return list(items) if self.expanded.get(key) else items[: self.limit]

    def row_count(self, key: str) -> int:
        return len(self.rows(key))

    def toggle_label(self, key: str) -> str:
        return SHOW_FEWER if self.expanded.get(key) else SHOW_ALL

    def toggle(self, key: str) -> bool:
        """Flip one column between all rows and the first few; returns the state."""
        if key not in self.items:
            return False
        if not self.can_expand(key):
            self.expanded[key] = False
            return False
        self.expanded[key] = not self.expanded.get(key, False)
        return self.expanded[key]

    def error_text(self, key: str) -> str:
        return self.errors.get(key, "")


# -- schedule ---------------------------------------------------------------
def air_at(entry: Any) -> float | None:
    """The timestamp an airing is pinned to, or ``None`` when it has none."""
    if isinstance(entry, Mapping):
        value = entry.get("air_at")
    else:
        value = getattr(entry, "air_at", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def minutes_text(stamp: datetime) -> str:
    """The local 24-hour clock time the schedule shows."""
    return stamp.strftime("%H:%M")


@dataclass
class ScheduleState:
    """Which week and day the schedule strip shows, and what airs then."""

    now: datetime = dataclass_field(default_factory=datetime.now)
    week_start: datetime = dataclass_field(init=False)
    selected: int = 0
    entries: list[Any] = dataclass_field(default_factory=list)

    def __post_init__(self) -> None:
        self.week_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)

    # clock -----------------------------------------------------------------
    def now_text(self) -> str:
        return f"Now: {self.now.strftime('%Y-%m-%d %H:%M')}"

    def set_now(self, when: datetime) -> None:
        self.now = when
        self.week_start = when.replace(hour=0, minute=0, second=0, microsecond=0)
        self.selected = 0

    def shift_week(self, days: int) -> None:
        self.week_start += timedelta(days=int(days))
        self.selected = 0

    def select_day(self, index: int) -> None:
        self.selected = max(0, min(DAYS_IN_WEEK - 1, int(index)))

    def selected_day(self) -> datetime:
        return self.week_start + timedelta(days=self.selected)

    def day_dates(self) -> list[date]:
        start = self.week_start.date()
        return [start + timedelta(days=index) for index in range(DAYS_IN_WEEK)]

    def is_today(self, index: int) -> bool:
        """Whether the chip at *index* is the day the clock says it is."""
        day = self.week_start.date() + timedelta(days=index)
        return day == self.now.date()

    # data ------------------------------------------------------------------
    def set_entries(self, entries: Iterable[Any]) -> None:
        """Air dates drive the list; each entry needs ``air_at``, title, episode."""
        self.entries = sorted(
            (entry for entry in entries if air_at(entry) is not None),
            key=lambda entry: air_at(entry) or 0.0,
        )

    def entries_for_day(self, moment: datetime | None = None) -> list[Any]:
        day = (moment or self.selected_day()).date()
        return [
            entry for entry in self.entries
            if datetime.fromtimestamp(air_at(entry) or 0.0).date() == day
        ]

    def rows(self) -> list[Any]:
        return self.entries_for_day()

    def empty_text(self) -> str:
        return f"Nothing airs on {self.selected_day().strftime('%A %d %B')}."


__all__ = [
    "BLANK_META",
    "COLUMN_HEADINGS",
    "COLUMN_KEYS",
    "COLUMN_LIMIT",
    "DAYS_IN_WEEK",
    "ERROR",
    "ERROR_PREFIX",
    "HIDDEN",
    "IDLE",
    "LOADED",
    "LOADING",
    "LOADING_TEXT",
    "PosterRowState",
    "SHOW_ALL",
    "SHOW_FEWER",
    "ScheduleState",
    "ThreeColumnState",
    "UNTITLED",
    "air_at",
    "error_line",
    "field",
    "minutes_text",
    "poster_text",
]
