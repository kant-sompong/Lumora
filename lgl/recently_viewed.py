"""Titles the user has opened, across games, movies, TV and anime.

``media_history`` records *playback* of TMDB titles inside the settings
document.  This store records *opening a title's page*, whatever kind it is,
so one list can offer "back to that game/movie/anime" on either desktop.

It lives in its own JSON file next to the library index, is written atomically,
keeps the most recent :data:`MAX_ENTRIES` entries and silently drops records it
cannot trust rather than showing half-populated rows.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

#: The kinds this history understands.  Unknown kinds are ignored.
KINDS = ("game", "movie", "tv", "anime")
#: Most recent titles kept; older entries fall off the end.
MAX_ENTRIES = 100
_MAX_TEXT = 300
_MAX_KEY = 200


def default_path() -> Path:
    """Where both frontends keep the history: the active profile's folder."""
    from . import profiles

    return profiles.active_file("recently-viewed.json")


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _entry(raw: Any) -> dict[str, Any] | None:
    """One stored record, or ``None`` when it cannot be trusted."""
    if not isinstance(raw, Mapping):
        return None
    kind = _text(raw.get("kind"), 20).casefold()
    key = _text(raw.get("key"), _MAX_KEY)
    if kind not in KINDS or not key:
        return None
    try:
        viewed_at = float(raw.get("viewed_at") or 0.0)
    except (TypeError, ValueError):
        return None
    poster = str(raw.get("poster_url") or "").strip()
    if poster and not poster.startswith(("https://", "http://")):
        poster = ""
    return {
        "kind": kind,
        "key": key,
        "title": _text(raw.get("title")) or key,
        "subtitle": _text(raw.get("subtitle"), 120),
        "poster_url": poster[:_MAX_TEXT],
        "viewed_at": viewed_at,
    }


def load(path: Path | str) -> list[dict[str, Any]]:
    """Every well-formed entry, newest first.

    A missing file, invalid JSON or an unexpected shape all read as an empty
    history: none of them is worth failing a page over.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("entries") if isinstance(raw, Mapping) else raw
    if not isinstance(items, list):
        return []
    entries = [entry for entry in (_entry(item) for item in items) if entry]
    entries.sort(key=lambda entry: entry["viewed_at"], reverse=True)
    return entries


def _write(path: Path | str, entries: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "entries": entries}, stream, ensure_ascii=False, indent=2)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def record(
    path: Path | str,
    *,
    kind: str,
    key: str,
    title: str,
    subtitle: str = "",
    poster_url: str = "",
    at: float | None = None,
) -> dict[str, Any] | None:
    """Remember that ``kind``/``key`` was opened.

    Opening the same title again moves it to the front instead of duplicating
    it.  Returns the stored entry, or ``None`` when kind or key is unusable.
    """
    clean_kind = _text(kind, 20).casefold()
    clean_key = _text(key, _MAX_KEY)
    if clean_kind not in KINDS or not clean_key:
        return None
    poster = str(poster_url or "").strip()
    entry = {
        "kind": clean_kind,
        "key": clean_key,
        "title": _text(title) or clean_key,
        "subtitle": _text(subtitle, 120),
        "poster_url": poster[:_MAX_TEXT] if poster.startswith(("https://", "http://")) else "",
        "viewed_at": float(at if at is not None else time.time()),
    }
    merged = [entry] + [
        item
        for item in load(path)
        if not (item["kind"] == clean_kind and item["key"] == clean_key)
    ]
    _write(path, merged[:MAX_ENTRIES])
    return entry


def recent(path: Path | str, limit: int = 12) -> list[dict[str, Any]]:
    """The most recently opened titles, at most ``limit``."""
    try:
        count = max(0, int(limit))
    except (TypeError, ValueError):
        count = 12
    return load(path)[:count]


def forget(path: Path | str, kind: str, key: str) -> bool:
    """Forget one title.  Returns whether anything was removed."""
    clean_kind = _text(kind, 20).casefold()
    clean_key = _text(key, _MAX_KEY)
    existing = load(path)
    kept = [
        item
        for item in existing
        if not (item["kind"] == clean_kind and item["key"] == clean_key)
    ]
    if len(kept) == len(existing):
        return False
    _write(path, kept)
    return True


def clear(path: Path | str) -> None:
    """Forget every title."""
    _write(path, [])


__all__ = [
    "KINDS",
    "MAX_ENTRIES",
    "clear",
    "default_path",
    "forget",
    "load",
    "recent",
    "record",
]
