"""Trailer handling: where a trailer lives, whether it may autoplay, and what
the user is told when there is nothing to play."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Field names a source may use for a trailer link, best first.
FIELDS = ("trailer", "trailer_url", "youtube_trailer", "video")


def trailer_url(item: Mapping[str, Any] | None) -> str:
    """The first http(s) trailer link on `item`, or an empty string."""
    for field in FIELDS:
        text = str((item or {}).get(field) or "").strip()
        if text.startswith(("http://", "https://")):
            return text
    return ""


def is_youtube(url: str) -> bool:
    lowered = str(url or "").lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def _flag(settings: Any, key: str, default: bool) -> bool:
    getter = getattr(settings, "get", None)
    if not callable(getter):
        return default
    try:
        value = getter(key)
    except Exception:  # noqa: BLE001
        return default
    return default if value is None else bool(value)


def autoplay_allowed(settings: Any) -> bool:
    """Trailers never autoplay unless the user turned it on."""
    return _flag(settings, "trailer_autoplay", False)


def muted_default(settings: Any) -> bool:
    return _flag(settings, "trailer_muted", True)


def plan(item: Mapping[str, Any] | None, settings: Any = None) -> dict[str, Any]:
    """What the player should do with this title's trailer."""
    url = trailer_url(item)
    if not url:
        return {"url": "", "autoplay": False, "muted": True, "reason": "No trailer for this title"}
    autoplay = autoplay_allowed(settings)
    return {
        "url": url,
        "autoplay": autoplay,
        "muted": muted_default(settings),
        "external": is_youtube(url),
        "reason": "Playing in the browser" if is_youtube(url) else "Playing in the app",
    }


def summary(item: Mapping[str, Any] | None) -> str:
    url = trailer_url(item)
    if not url:
        return "No trailer"
    return "YouTube trailer" if is_youtube(url) else "Trailer"


__all__ = [
    "FIELDS",
    "autoplay_allowed",
    "is_youtube",
    "muted_default",
    "plan",
    "summary",
    "trailer_url",
]
