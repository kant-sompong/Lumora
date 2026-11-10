"""Playback history for Movies & TV, stored in the shared settings document.

The record is deliberately small and honest: it says *that* a title was opened
for playback and when, plus the metadata needed to draw its card again.  It
never invents a progress percentage, because the embedded frameXTV player does
not expose reliable playback position to this application.

Nothing here touches the network.  An entry is written only when the user
presses a Play button, and the store is capped so it cannot grow without bound.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

HISTORY_KEY = "media_history"
#: Most recent titles kept.  Older entries fall off the end.
MAX_HISTORY = 60
#: One entry is this many fields; anything larger is malformed and dropped.
_MAX_TITLE = 300
_MAX_URL = 500


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _poster(value: Any) -> str:
    """An absolute http(s) poster URL, or empty when the value is not one.

    Older records may spell the field ``poster``, and a poster hosted over
    plain http is still a poster worth drawing, so both are accepted while a
    relative path is not: the card loader can only fetch a full URL.
    """
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    if not lowered.startswith(("http://", "https://")):
        return ""
    return text[:_MAX_URL]


def _valid_key(value: Any) -> str:
    text = str(value or "").strip()
    kind, _, number = text.partition(":")
    if kind not in ("movie", "tv"):
        return ""
    if not number.isdigit() or int(number) <= 0:
        return ""
    return f"{kind}:{int(number)}"


#: Public alias so other stores can validate keys without copying the rule.
valid_key = _valid_key


def _entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    key = _valid_key(raw.get("key"))
    if not key:
        return None
    kind, _, number = key.partition(":")
    try:
        watched_at = float(raw.get("watched_at") or 0.0)
    except (TypeError, ValueError):
        watched_at = 0.0
    # ``poster`` is what records written before the field was named
    # ``poster_url`` used; reading it keeps those rows drawing their artwork.
    poster = _poster(raw.get("poster_url") or raw.get("poster"))
    return {
        "key": key,
        "kind": kind,
        "tmdb_id": int(number),
        "title": _clean_text(raw.get("title"), _MAX_TITLE) or key,
        "poster_url": poster,
        "year": _clean_text(raw.get("year"), 4),
        "watched_at": watched_at,
    }


def load(settings: Any) -> list[dict[str, Any]]:
    """Every well-formed history entry, newest first.

    Malformed records are dropped rather than returned as half-populated rows.
    """
    raw = settings.get(HISTORY_KEY, []) if settings is not None else []
    if not isinstance(raw, list):
        return []
    entries = [entry for entry in (_entry(item) for item in raw) if entry]
    entries.sort(key=lambda entry: entry["watched_at"], reverse=True)
    return entries


def record(
    settings: Any,
    *,
    key: str,
    title: str,
    poster_url: str = "",
    year: str = "",
    watched_at: float | None = None,
) -> dict[str, Any] | None:
    """Remember that ``key`` was opened for playback.

    Re-watching a title moves it to the front instead of duplicating it.
    Returns the stored entry, or ``None`` when the key is unusable.
    """
    if settings is None:
        return None
    clean = _valid_key(key)
    if not clean:
        return None
    kind, _, number = clean.partition(":")
    existing = load(settings)
    previous = next((item for item in existing if item["key"] == clean), None)
    entry = {
        "key": clean,
        "kind": kind,
        "tmdb_id": int(number),
        "title": _clean_text(title, _MAX_TITLE) or clean,
        # A play that has no poster of its own (an episode row, a search
        # result) keeps the artwork already stored for that title instead of
        # replacing it with nothing.
        "poster_url": _poster(poster_url) or str((previous or {}).get("poster_url") or ""),
        "year": _clean_text(year, 4),
        "watched_at": float(watched_at if watched_at is not None else time.time()),
    }
    merged = [entry] + [item for item in existing if item["key"] != clean]
    settings.set(HISTORY_KEY, merged[:MAX_HISTORY])
    return entry


def recent(settings: Any, limit: int = 12) -> list[dict[str, Any]]:
    """The most recently played titles, at most ``limit``."""
    try:
        count = max(0, int(limit))
    except (TypeError, ValueError):
        count = 12
    return load(settings)[:count]


def remove(settings: Any, key: str) -> bool:
    """Forget one title.  Returns whether anything was removed."""
    clean = _valid_key(key)
    if settings is None or not clean:
        return False
    existing = load(settings)
    kept = [item for item in existing if item["key"] != clean]
    if len(kept) == len(existing):
        return False
    settings.set(HISTORY_KEY, kept)
    return True


def clear(settings: Any) -> None:
    if settings is not None:
        settings.set(HISTORY_KEY, [])


def keys(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(entry.get("key") or "") for entry in entries]


__all__ = [
    "HISTORY_KEY",
    "MAX_HISTORY",
    "clear",
    "keys",
    "load",
    "recent",
    "record",
    "remove",
]
