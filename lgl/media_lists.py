"""Media favourites and the Continue Watching shelf.

Both lists live in the shared settings document and use the same
``movie:<tmdb_id>`` / ``tv:<tmdb_id>`` keys as the playback history, so a title
can never appear twice under two spellings.

The player is an embedded web frame that reports nothing about playback, which
is why a position is only stored when a caller actually measured one.  Nothing
here infers "finished" from missing data: an entry is resumable only while it
carries a position that is both past the opening seconds and short of the
duration we know about.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from .media_history import valid_key

#: Settings keys.  They are deliberately separate from ``media_history`` so a
#: cleared history does not silently empty the user's favourites.
FAVORITES_KEY = "media_favorites"
CONTINUE_KEY = "continue_watching"

MAX_FAVORITES = 200
MAX_CONTINUE = 40
#: Positions below this are noise (an accidental click), not progress.
MIN_RESUME_SECONDS = 5.0
#: At or past this fraction of the duration the title is treated as finished.
NEAR_END_RATIO = 0.95
_MAX_TITLE = 300
_MAX_URL = 500


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _seconds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number < 0:  # NaN or negative
        return 0.0
    return number


# -- favourites ---------------------------------------------------------------


def favorites(settings: Any) -> list[str]:
    """Stored favourite keys, newest first, de-duplicated and validated."""
    if settings is None:
        return []
    raw = settings.get(FAVORITES_KEY, [])
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for item in raw:
        key = valid_key(item)
        if key and key not in seen:
            seen.append(key)
    return seen


def is_favorite(settings: Any, key: str) -> bool:
    clean = valid_key(key)
    return bool(clean) and clean in favorites(settings)


def set_favorite(settings: Any, key: str, favorite: bool) -> bool:
    """Add or remove one key.  Returns whether the stored list changed."""
    clean = valid_key(key)
    if settings is None or not clean:
        return False
    current = favorites(settings)
    if favorite:
        if clean in current:
            return False
        updated = ([clean] + current)[:MAX_FAVORITES]
    else:
        if clean not in current:
            return False
        updated = [item for item in current if item != clean]
    settings.set(FAVORITES_KEY, updated)
    return True


def toggle_favorite(settings: Any, key: str) -> bool:
    """Flip one key and report the state afterwards."""
    clean = valid_key(key)
    if not clean:
        return False
    now_favorite = not is_favorite(settings, clean)
    set_favorite(settings, clean, now_favorite)
    return now_favorite


# -- continue watching --------------------------------------------------------


def _continue_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    key = valid_key(raw.get("key"))
    if not key:
        return None
    kind, _, number = key.partition(":")
    poster = str(raw.get("poster_url") or "").strip()
    if poster and not poster.startswith("https://"):
        poster = ""
    return {
        "key": key,
        "kind": kind,
        "tmdb_id": int(number),
        "title": _clean(raw.get("title"), _MAX_TITLE) or key,
        "poster_url": poster[:_MAX_URL],
        "year": _clean(raw.get("year"), 4),
        "position": _seconds(raw.get("position")),
        "duration": _seconds(raw.get("duration")),
        "updated_at": _seconds(raw.get("updated_at")),
    }


def _is_resumable(entry: Mapping[str, Any]) -> bool:
    position = _seconds(entry.get("position"))
    duration = _seconds(entry.get("duration"))
    if position < MIN_RESUME_SECONDS:
        return False
    if duration > 0 and position >= duration * NEAR_END_RATIO:
        return False
    return True


def stored(settings: Any) -> list[dict[str, Any]]:
    """Every well-formed shelf entry, newest first."""
    if settings is None:
        return []
    raw = settings.get(CONTINUE_KEY, [])
    if not isinstance(raw, list):
        return []
    entries = [entry for entry in (_continue_entry(item) for item in raw) if entry]
    entries.sort(key=lambda entry: entry["updated_at"], reverse=True)
    return entries


def shelf(settings: Any, limit: int = 12) -> list[dict[str, Any]]:
    """Resumable titles for the Continue Watching shelf, newest first."""
    try:
        count = max(0, int(limit))
    except (TypeError, ValueError):
        count = 12
    return [entry for entry in stored(settings) if _is_resumable(entry)][:count]


def record_position(
    settings: Any,
    *,
    key: str,
    position: float,
    duration: float | None = None,
    title: str = "",
    poster_url: str = "",
    year: str = "",
    updated_at: float | None = None,
) -> dict[str, Any] | None:
    """Store measured progress for one title.

    ``position`` is seconds into the title.  A finished title is dropped from the
    shelf rather than stored at 100 %, because there is nothing left to resume.
    """
    clean = valid_key(key)
    if settings is None or not clean:
        return None
    kind, _, number = clean.partition(":")
    poster = str(poster_url or "").strip()
    entry = {
        "key": clean,
        "kind": kind,
        "tmdb_id": int(number),
        "title": _clean(title, _MAX_TITLE) or clean,
        "poster_url": poster[:_MAX_URL] if poster.startswith("https://") else "",
        "year": _clean(year, 4),
        "position": _seconds(position),
        "duration": _seconds(duration),
        "updated_at": float(updated_at if updated_at is not None else time.time()),
    }
    kept = [item for item in stored(settings) if item["key"] != clean]
    if not _is_resumable(entry):
        settings.set(CONTINUE_KEY, kept[:MAX_CONTINUE])
        return None
    settings.set(CONTINUE_KEY, ([entry] + kept)[:MAX_CONTINUE])
    return entry


def dismiss(settings: Any, key: str) -> bool:
    """Remove one title from the shelf.  Returns whether it was there."""
    clean = valid_key(key)
    if settings is None or not clean:
        return False
    current = stored(settings)
    kept = [item for item in current if item["key"] != clean]
    if len(kept) == len(current):
        return False
    settings.set(CONTINUE_KEY, kept)
    return True


def clear(settings: Any) -> None:
    if settings is not None:
        settings.set(CONTINUE_KEY, [])


__all__ = [
    "CONTINUE_KEY",
    "FAVORITES_KEY",
    "MAX_CONTINUE",
    "MAX_FAVORITES",
    "MIN_RESUME_SECONDS",
    "NEAR_END_RATIO",
    "clear",
    "dismiss",
    "favorites",
    "is_favorite",
    "record_position",
    "set_favorite",
    "shelf",
    "stored",
    "toggle_favorite",
]
