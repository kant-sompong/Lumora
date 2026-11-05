"""Numbers about a collection: an activity heatmap, totals, favourite genres,
a recent timeline and a split between games and media.

Events are plain dicts with ``ts`` (unix seconds or ISO text) and optionally
``kind`` (``game``/``media``); the caller decides where they come from.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

DAY = 86400
WEEK = 7 * DAY


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.UTC)
    return parsed.timestamp()


def _day(value: Any) -> _dt.date | None:
    stamp = _timestamp(value)
    if stamp is None:
        return None
    return _dt.datetime.fromtimestamp(stamp, tz=_dt.UTC).date()


def heatmap(events: Iterable[Mapping[str, Any]], weeks: int = 12, *, today: Any = None) -> list[list[int]]:
    """Counts per weekday row over the last `weeks` weeks, Monday first.

    Gaps are zero, so a frontend can draw the grid without checking for
    missing days.
    """
    span = max(1, int(weeks))
    end = _day(today) or _dt.datetime.now(tz=_dt.UTC).date()
    start = end - _dt.timedelta(days=span * 7 - 1)
    counts = Counter(
        day for day in (_day(event.get("ts")) for event in events) if day is not None
    )
    grid = [[0] * span for _ in range(7)]
    for offset in range(span * 7):
        day = start + _dt.timedelta(days=offset)
        if day > end:
            break
        grid[day.weekday()][offset // 7] += counts.get(day, 0)
    return grid


def busiest_day(events: Iterable[Mapping[str, Any]], *, today: Any = None) -> tuple[str, int] | None:
    counts = Counter(
        day for day in (_day(event.get("ts")) for event in events) if day is not None
    )
    if not counts:
        return None
    day, count = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
    return day.isoformat(), count


def totals(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Event count, active days and the longest streak of consecutive days."""
    rows = list(events)
    days = sorted({day for day in (_day(event.get("ts")) for event in rows) if day is not None})
    streak = 0
    best = 0
    previous: _dt.date | None = None
    for day in days:
        streak = streak + 1 if previous and (day - previous).days == 1 else 1
        best = max(best, streak)
        previous = day
    return {"events": len(rows), "days": len(days), "streak": best}


def favourite_genres(items: Iterable[Mapping[str, Any]], limit: int = 8) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for item in items:
        raw = item.get("genres") or ()
        if isinstance(raw, str):
            raw = raw.replace(";", ",").split(",")
        for genre in raw:
            text = str(genre).strip()
            if text:
                counter[text] += 1
    return counter.most_common(max(1, int(limit)))


def timeline(
    entries: Iterable[Mapping[str, Any]],
    limit: int = 20,
) -> list[tuple[str, str]]:
    """``(day, label)`` pairs, newest first."""
    rows: list[tuple[float, str, str]] = []
    for entry in entries:
        stamp = _timestamp(entry.get("ts"))
        if stamp is None:
            continue
        label = str(entry.get("title") or entry.get("label") or entry.get("kind") or "Activity").strip()
        rows.append((stamp, _dt.datetime.fromtimestamp(stamp, tz=_dt.UTC).date().isoformat(), label))
    rows.sort(key=lambda row: (-row[0], row[2]))
    return [(day, label) for _stamp, day, label in rows[: max(1, int(limit))]]


def split(entries: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Games and media kept apart, so one view can show both."""
    buckets: dict[str, list[Mapping[str, Any]]] = {"game": [], "media": []}
    for entry in entries:
        kind = str(entry.get("kind") or "").strip().lower()
        if kind in ("movie", "show", "anime", "episode"):
            kind = "media"
        buckets.setdefault(kind or "other", []).append(entry)
    return buckets


def coverage(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many titles carry which metadata; drives the "metadata gaps" view."""
    fields = ("poster", "description", "year", "genres", "rating")
    counts = {field: 0 for field in fields}
    total = 0
    for item in items:
        total += 1
        for field in fields:
            if item.get(field):
                counts[field] += 1
    counts["titles"] = total
    return counts


__all__ = [
    "DAY",
    "WEEK",
    "busiest_day",
    "coverage",
    "favourite_genres",
    "heatmap",
    "split",
    "timeline",
    "totals",
]
