"""The best playable trailer for a TMDB movie or show.

TMDB's ``/movie/{id}/videos`` and ``/tv/{id}/videos`` endpoints return every
clip a title has, including featurettes, bloopers and clips hosted somewhere
other than YouTube.  This module narrows that to one thing the app can embed:

* only ``site: "YouTube"`` rows, and only a valid YouTube id (the embed URL is
  built here, so a cached or injected URL can never point anywhere else);
* an official **Trailer** first, then an official Teaser, then any Trailer,
  newest first within the same rank;
* the embed URL is always ``https://www.youtube-nocookie.com/embed/<id>``.

The HTTP read is injected (``fetch(path)``), and answers are cached for a day in
``cache_dir()/trailers.json`` beside the other caches, because trailers change
rarely.  Failures become one plain sentence, like the season and calendar data.
Nothing here imports a GUI toolkit.
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import platform_support, tmdb

EMBED_BASE = "https://www.youtube-nocookie.com/embed/"
#: YouTube ids are 11 characters; a little slack keeps older ids working.
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
CACHE_FILENAME = "trailers.json"
#: A day: a new trailer for an old title is rare, and a stale one still plays.
CACHE_TTL = 24 * 3600
KINDS = ("movie", "tv")
VIDEO_TYPES = ("trailer", "teaser")

Fetch = Callable[[str], Any]


class TrailerError(Exception):
    """The trailer lookup failed, in a way the window can print."""


def clean_kind(kind: Any) -> str:
    text = str(kind or "").strip().lower()
    return text if text in KINDS else ""


def clean_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def youtube_key(value: Any) -> str:
    """A safe YouTube id, or ``""`` for anything that is not one."""
    text = str(value or "").strip()
    return text if VIDEO_ID.match(text) else ""


def embed_url(key: Any, *, autoplay: bool = False, muted: bool = True) -> str:
    """The only URL the app will ever embed for a trailer."""
    ident = youtube_key(key)
    if not ident:
        return ""
    url = f"{EMBED_BASE}{ident}"
    params: list[str] = []
    if autoplay:
        params.append("autoplay=1")
        if muted:
            params.append("mute=1")
    return f"{url}?{'&'.join(params)}" if params else url


def _rank(video: Mapping[str, Any]) -> tuple[bool, bool, str]:
    """Official first, then a Trailer over a Teaser, then the newest."""
    return (
        video.get("official") is True,
        str(video.get("type") or "").casefold() == "trailer",
        str(video.get("published_at") or ""),
    )


def pick_trailer(videos: Iterable[Any]) -> dict[str, Any] | None:
    """The best embeddable trailer on TMDB's video list, or ``None``.

    Only YouTube rows with a valid id survive; every other site, type or key is
    rejected rather than passed through.
    """
    best: dict[str, Any] | None = None
    best_rank: tuple[bool, bool, str] | None = None
    for row in videos if isinstance(videos, Iterable) else []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("site") or "").strip().casefold() != "youtube":
            continue
        if str(row.get("type") or "").strip().casefold() not in VIDEO_TYPES:
            continue
        key = youtube_key(row.get("key"))
        if not key:
            continue
        candidate = {
            "key": key,
            "name": " ".join(str(row.get("name") or "").split())[:200] or "Trailer",
            "type": "Teaser" if str(row.get("type") or "").casefold() == "teaser" else "Trailer",
            "official": row.get("official") is True,
        }
        rank = _rank(candidate)
        if best_rank is None or rank > best_rank:
            best, best_rank = candidate, rank
    return best


def _stored(record: Any) -> dict[str, Any] | None:
    """Rebuild a cached trailer from its id, never from a stored URL."""
    if not isinstance(record, Mapping):
        return None
    key = youtube_key(record.get("key"))
    if not key:
        return None
    return {
        "key": key,
        "name": " ".join(str(record.get("name") or "").split())[:200] or "Trailer",
        "type": "Teaser" if str(record.get("type") or "").casefold() == "teaser" else "Trailer",
        "official": record.get("official") is True,
    }


# -- cache -----------------------------------------------------------------

def default_cache_path() -> Path:
    return platform_support.cache_dir() / CACHE_FILENAME


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    videos = data.get("videos")
    return videos if isinstance(videos, dict) else {}


def _save_cache(path: Path, videos: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "videos": dict(videos)}, indent=2, sort_keys=True),
            "utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass  # a cache that cannot be written is not an error


def reason_of(exc: BaseException) -> str:
    """One plain sentence for a failed lookup, never a traceback."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "the request timed out"
    if isinstance(exc, urllib.error.URLError):
        return f"network error ({getattr(exc, 'reason', exc)})"
    text = " ".join(str(exc).split())
    return text or exc.__class__.__name__


@dataclass
class TrailerResult:
    """The trailer for one title, or the reason there is none."""

    trailer: dict[str, Any] | None = None
    error: str = ""
    from_cache: bool = False
    fetched_at: float = 0.0
    source: str = "TMDB"

    @property
    def has_trailer(self) -> bool:
        return self.trailer is not None

    @property
    def error_text(self) -> str:
        return f"Could not load: {self.error}" if self.error else ""

    def embed(self, *, autoplay: bool = False, muted: bool = True) -> str:
        if not self.trailer:
            return ""
        return embed_url(self.trailer.get("key"), autoplay=autoplay, muted=muted)

    def label(self) -> str:
        if not self.trailer:
            return "Trailer"
        return str(self.trailer.get("type") or "Trailer")


def _default_fetch(settings: Any) -> Fetch:
    client = tmdb.TmdbClient(tmdb.load_api_key(settings=settings))

    def fetch(path: str) -> Any:
        return client.get(path)

    return fetch


def load_trailer(
    kind: Any,
    tmdb_id: Any,
    *,
    fetch: Fetch | None = None,
    settings: Any = None,
    now: float | None = None,
    ttl: float = CACHE_TTL,
    cache_path: Path | str | None = None,
    force: bool = False,
) -> TrailerResult:
    """The best trailer for ``kind``/``tmdb_id``, cached or freshly fetched."""
    chosen = clean_kind(kind)
    number = clean_id(tmdb_id)
    if not chosen or number is None:
        return TrailerResult(error="This title has no movie or show id.")
    path = Path(cache_path) if cache_path is not None else default_cache_path()
    current = time.time() if now is None else float(now)
    key = f"{chosen}:{number}"
    videos = _load_cache(path)
    record = videos.get(key)
    fetched_at = 0.0
    if isinstance(record, Mapping):
        try:
            fetched_at = float(record.get("fetched_at"))
        except (TypeError, ValueError):
            fetched_at = 0.0

    fresh = isinstance(record, Mapping) and 0 <= current - fetched_at < ttl
    if fresh and not force:
        return TrailerResult(
            trailer=_stored(record.get("trailer")),
            from_cache=True,
            fetched_at=fetched_at,
        )

    sender = fetch if fetch is not None else _default_fetch(settings)
    endpoint = f"/{chosen}/{number}/videos"
    try:
        payload = sender(endpoint)
    except Exception as exc:  # noqa: BLE001 - offline is normal for this window
        message = f"TMDB ({endpoint}): {reason_of(exc)}"
        if isinstance(record, Mapping):
            return TrailerResult(
                trailer=_stored(record.get("trailer")),
                error=message,
                from_cache=True,
                fetched_at=fetched_at,
            )
        return TrailerResult(error=message)

    rows = payload.get("results") if isinstance(payload, Mapping) else None
    trailer = pick_trailer(rows or [])
    videos[key] = {"fetched_at": current, "trailer": trailer}
    _save_cache(path, videos)
    return TrailerResult(trailer=trailer, fetched_at=current)


__all__ = [
    "CACHE_FILENAME",
    "CACHE_TTL",
    "EMBED_BASE",
    "KINDS",
    "TrailerError",
    "TrailerResult",
    "VIDEO_ID",
    "VIDEO_TYPES",
    "clean_id",
    "clean_kind",
    "default_cache_path",
    "embed_url",
    "load_trailer",
    "pick_trailer",
    "reason_of",
    "youtube_key",
]
