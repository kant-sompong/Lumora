"""Release calendar for followed anime.

Roadmap section 8: show upcoming episodes, let users follow titles, be
timezone-aware, cope with unknown or delayed dates, and only notify once the
calendar is trustworthy.

Dates arrive from community data in several shapes, so everything is normalised
to an epoch and rendered in the viewer's zone.  An entry without a usable date
is shown as unknown rather than guessed at.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # zoneinfo ships with 3.9+, but a slim Linux image can lack tzdata.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - only on exotic builds
    ZoneInfo = None  # type: ignore[assignment]

    class ZoneInfoNotFoundError(Exception):  # type: ignore[no-redef]
        pass

FOLLOW_KEY = "anime_follows"
MAX_FOLLOWS = 120
DEFAULT_DAYS = 7
MAX_DAYS = 60
NOTIFY_WINDOW = 6 * 3600
UNKNOWN_LABEL = "Release date unknown"


def zone(name: str):
    """A usable timezone, falling back to UTC instead of raising."""
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(str(name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def parse_when(value: Any) -> float | None:
    """Normalise whatever the provider sent into an epoch, or ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if seconds > 0 else None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(text) or None
    cleaned = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


@dataclass(frozen=True)
class Entry:
    key: str
    title: str
    episode: str = ""
    air_at: float | None = None
    source: str = ""
    delayed: bool = False
    changed: bool = False

    @property
    def known(self) -> bool:
        return self.air_at is not None

    def in_zone(self, tz: str = "UTC") -> datetime:
        stamp = self.air_at if self.air_at is not None else 0.0
        return datetime.fromtimestamp(stamp, tz=zone(tz))


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def entry_from(raw: Mapping[str, Any]) -> Entry | None:
    title = _clean(raw.get("title"), 200)
    if not title:
        return None
    key = _clean(raw.get("key") or raw.get("id") or raw.get("mal_id"), 80) or title.casefold()
    return Entry(
        key=key,
        title=title,
        episode=_clean(raw.get("episode"), 40),
        air_at=parse_when(raw.get("air_at") or raw.get("airing_at") or raw.get("date")),
        source=_clean(raw.get("source"), 40),
        delayed=bool(raw.get("delayed", False)),
        changed=bool(raw.get("changed", False)),
    )


def follows(settings: Any) -> list[str]:
    raw = settings.get(FOLLOW_KEY) if settings is not None else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = _clean(item, 80)
        if text and text not in out:
            out.append(text)
    return out


def is_following(settings: Any, key: str) -> bool:
    target = _clean(key, 80)
    return bool(target) and target in follows(settings)


def follow(settings: Any, key: str) -> bool:
    target = _clean(key, 80)
    if not target:
        return False
    current = follows(settings)
    if target in current:
        return True
    if len(current) >= MAX_FOLLOWS:
        return False
    current.append(target)
    settings.set(FOLLOW_KEY, current)
    return True


def unfollow(settings: Any, key: str) -> bool:
    target = _clean(key, 80)
    current = follows(settings)
    if target not in current:
        return False
    settings.set(FOLLOW_KEY, [item for item in current if item != target])
    return True


def upcoming(
    items: Iterable[Any],
    *,
    now: float | None = None,
    days: int = DEFAULT_DAYS,
    follows_only: Sequence[str] | None = None,
    limit: int = 60,
) -> list[Entry]:
    """Episodes airing between now and ``days`` ahead, soonest first.

    Entries with no usable date come last and are kept, so the UI can list them
    under an "unknown" heading instead of hiding them.
    """
    current = time.time() if now is None else float(now)
    try:
        horizon_days = max(0, min(MAX_DAYS, int(days)))
    except (TypeError, ValueError):
        horizon_days = DEFAULT_DAYS
    horizon = current + horizon_days * 86400
    wanted = {str(item) for item in (follows_only or [])}

    dated: list[Entry] = []
    unknown: list[Entry] = []
    for raw in items:
        entry = raw if isinstance(raw, Entry) else entry_from(raw if isinstance(raw, Mapping) else {})
        if entry is None:
            continue
        if wanted and entry.key not in wanted and entry.title not in wanted:
            continue
        if entry.air_at is None:
            unknown.append(entry)
            continue
        if current <= entry.air_at <= horizon:
            dated.append(entry)
    dated.sort(key=lambda item: (item.air_at or 0.0, item.title.casefold()))
    unknown.sort(key=lambda item: item.title.casefold())
    merged = dated + unknown
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = 60
    return merged[:cap] if cap else []


def format_when(entry: Entry, tz: str = "UTC") -> str:
    if not entry.known:
        return UNKNOWN_LABEL
    moment = entry.in_zone(tz)
    label = moment.strftime("%a %d %b %H:%M")
    return f"{label} {moment.tzname() or tz}"


def describe(entry: Entry, tz: str = "UTC") -> str:
    bits = [entry.title]
    if entry.episode:
        bits.append(f"episode {entry.episode}")
    bits.append("airs " + format_when(entry, tz) if entry.known else UNKNOWN_LABEL)
    if entry.delayed:
        bits.append("(delayed)")
    if entry.changed:
        bits.append("(date changed)")
    return " ".join(bits)


def grouped(entries: Sequence[Entry], tz: str = "UTC") -> list[tuple[str, list[Entry]]]:
    """Split a calendar into day headings for the UI."""
    buckets: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.known:
            label = entry.in_zone(tz).strftime("%A %d %B")
        else:
            label = UNKNOWN_LABEL
        buckets.setdefault(label, []).append(entry)
    return list(buckets.items())


def notify_due(
    entries: Iterable[Entry],
    *,
    now: float | None = None,
    notify: Callable[[str, str], Any] | None = None,
    deduper: Any = None,
    window: float = NOTIFY_WINDOW,
) -> int:
    """Announce followed episodes airing within ``window`` seconds.

    Returns how many notifications were sent.  A missing notifier is not an
    error — it just means nothing is announced.
    """
    if notify is None:
        return 0
    current = time.time() if now is None else float(now)
    sent = 0
    for entry in entries:
        if not entry.known or entry.air_at is None:
            continue
        delta = entry.air_at - current
        if delta < -window or delta > window:
            continue
        token = f"anime:{entry.key}:{entry.episode}"
        if deduper is not None:
            allows = getattr(deduper, "allows", None)
            if callable(allows) and not allows(token, current):
                continue
        title = f"{entry.title} episode {entry.episode}" if entry.episode else entry.title
        notify(title, "Airs " + format_when(entry))
        sent += 1
    return sent


__all__ = [
    "DEFAULT_DAYS",
    "Entry",
    "FOLLOW_KEY",
    "MAX_DAYS",
    "MAX_FOLLOWS",
    "NOTIFY_WINDOW",
    "UNKNOWN_LABEL",
    "describe",
    "entry_from",
    "follow",
    "follows",
    "format_when",
    "grouped",
    "is_following",
    "notify_due",
    "parse_when",
    "unfollow",
    "upcoming",
    "zone",
]
