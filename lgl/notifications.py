"""Desktop notifications for finished downloads and new episodes.

Each category has its own switch, and the default differs on purpose: downloads
announce themselves because the queue is what the user is waiting for, episodes
stay quiet until the calendar has proved itself.

A notification is best-effort.  ``notify-send`` may be missing, a Windows toast
may be blocked by policy, and none of that is an error worth interrupting a
download for, so every call returns a boolean instead of raising.
"""

from __future__ import annotations

import time
from typing import Any

from .platform_support import notify

NOTIFY_DOWNLOADS = "notify_downloads"
NOTIFY_EPISODES = "notify_episodes"
CATEGORIES = (NOTIFY_DOWNLOADS, NOTIFY_EPISODES)
#: Seconds during which the same message is not repeated.
DEDUPE_WINDOW = 60.0
_MAX_REMEMBERED = 32


def _default(category: str) -> bool:
    return category == NOTIFY_DOWNLOADS


def enabled(settings: Any, category: str) -> bool:
    """Whether this category is switched on, falling back to its default."""
    if settings is None:
        return False
    try:
        value = settings.get(category, None)
    except Exception:
        return _default(category)
    if value is None:
        return _default(category)
    return bool(value)


def set_enabled(settings: Any, category: str, value: bool) -> None:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown notification category: {category}")
    if settings is not None:
        settings.set(category, bool(value))


class Deduper:
    """Remembers recent messages so a retry loop cannot spam the desktop."""

    def __init__(self, window: float = DEDUPE_WINDOW) -> None:
        self.window = max(0.0, float(window))
        self._seen: dict[str, float] = {}

    def allows(self, signature: str, now: float) -> bool:
        last = self._seen.get(signature)
        if last is not None and now - last < self.window:
            return False
        self._seen[signature] = now
        if len(self._seen) > _MAX_REMEMBERED:
            oldest = sorted(self._seen.items(), key=lambda item: item[1])
            for key, _ in oldest[: len(self._seen) - _MAX_REMEMBERED]:
                self._seen.pop(key, None)
        return True


_default_deduper = Deduper()


def _send(
    settings: Any,
    category: str,
    title: str,
    message: str,
    *,
    signature: str,
    at: float | None,
    dedupe: Deduper | None,
    error: bool = False,
) -> bool:
    if not enabled(settings, category):
        return False
    moment = float(at if at is not None else time.time())
    guard = dedupe or _default_deduper
    if not guard.allows(signature, moment):
        return False
    return bool(notify(title, message, error=error))


def download_finished(
    settings: Any,
    filename: str,
    *,
    at: float | None = None,
    dedupe: Deduper | None = None,
) -> bool:
    name = " ".join(str(filename or "").split()) or "Download"
    return _send(
        settings,
        NOTIFY_DOWNLOADS,
        "Download finished",
        name,
        signature=f"done:{name}",
        at=at,
        dedupe=dedupe,
    )


def download_failed(
    settings: Any,
    filename: str,
    error: str | None = None,
    *,
    at: float | None = None,
    dedupe: Deduper | None = None,
) -> bool:
    name = " ".join(str(filename or "").split()) or "Download"
    detail = " ".join(str(error or "").split())
    message = f"{name} — {detail}" if detail else name
    return _send(
        settings,
        NOTIFY_DOWNLOADS,
        "Download failed",
        message[:200],
        signature=f"failed:{name}:{detail[:40]}",
        at=at,
        dedupe=dedupe,
        error=True,
    )


def episode_released(
    settings: Any,
    title: str,
    *,
    at: float | None = None,
    dedupe: Deduper | None = None,
) -> bool:
    name = " ".join(str(title or "").split()) or "Anime"
    return _send(
        settings,
        NOTIFY_EPISODES,
        "New episode",
        name,
        signature=f"episode:{name}",
        at=at,
        dedupe=dedupe,
    )


__all__ = [
    "CATEGORIES",
    "DEDUPE_WINDOW",
    "NOTIFY_DOWNLOADS",
    "NOTIFY_EPISODES",
    "Deduper",
    "download_failed",
    "download_finished",
    "enabled",
    "episode_released",
    "set_enabled",
]
