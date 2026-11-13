"""Ratings for one title: IMDb, Rotten Tomatoes and TMDB's user score.

OMDb is the only new network source this feature adds, and it is optional: with
no key the module still reports the TMDB percentage, and with no answer it
returns what it has.  A failed or slow request never raises and never blocks a
page: the caller renders whatever came back and shows nothing otherwise.

This module deliberately imports no GUI toolkit; the Linux and Windows apps both
use it so the numbers and their parsing cannot diverge.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

OMDB_ENV = "OMDB_API_KEY"
OMDB_SETTING = "omdb_api_key"
OMDB_URL = "https://www.omdbapi.com/"
OMDB_HOST = "www.omdbapi.com"
#: How long one IMDb id's OMDb answer is trusted, in seconds (7 days).
CACHE_TTL = 7 * 24 * 60 * 60
CACHE_NAME = "ratings.json"
REQUEST_TIMEOUT = 6.0
MAX_BODY_BYTES = 256 * 1024


@dataclass(frozen=True)
class Ratings:
    """What is known about one title's scores.  ``None`` means "not reported"."""

    imdb: float | None = None
    rotten: int | None = None
    tmdb_percent: int | None = None

    @property
    def empty(self) -> bool:
        return self.imdb is None and self.rotten is None and self.tmdb_percent is None


def _number(value: Any) -> float | None:
    """``"8.5"`` or ``"8.5/10"`` -> 8.5; anything else -> None."""
    text = str(value or "").strip()
    if not text or text.upper() in {"N/A", "NA", "-"}:
        return None
    text = text.split("/", 1)[0].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if number <= 0 or number > 10:
        return None
    return round(number, 1)


def _percent(value: Any) -> int | None:
    """``"90%"`` -> 90; out-of-range or missing -> None."""
    text = str(value or "").strip()
    if not text.endswith("%"):
        return None
    try:
        number = int(float(text[:-1].strip()))
    except ValueError:
        return None
    return number if 0 <= number <= 100 else None


def _rotten_from_list(value: Any) -> int | None:
    """OMDb's ``Ratings`` list, where Rotten Tomatoes appears by name."""
    if not isinstance(value, (list, tuple)):
        return None
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        if "rotten" not in str(entry.get("Source") or "").casefold():
            continue
        return _percent(entry.get("Value"))
    return None


def parse_omdb(payload: Any) -> Ratings:
    """Read OMDb's response, tolerating the shapes it actually sends."""
    if not isinstance(payload, Mapping):
        return Ratings()
    if str(payload.get("Response") or "").strip().casefold() == "false":
        return Ratings()
    return Ratings(
        imdb=_number(payload.get("imdbRating")),
        rotten=_rotten_from_list(payload.get("Ratings")),
    )


def tmdb_percent(vote_average: Any, vote_count: Any) -> int | None:
    """TMDB's 0-10 average as a whole percentage, or None without real votes."""
    try:
        average = float(vote_average)
        votes = int(vote_count)
    except (TypeError, ValueError):
        return None
    if votes <= 0 or average <= 0:
        return None
    percent = round(average * 10)
    return percent if 0 < percent <= 100 else None


def read_omdb_key(settings: Any = None, env: Mapping[str, str] | None = None) -> str:
    """``OMDB_API_KEY`` first, then the settings store, exactly like TMDB."""
    source = os.environ if env is None else env
    value = str(source.get(OMDB_ENV) or "").strip()
    if value:
        return value
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            stored = getter(OMDB_SETTING, "")
        except Exception:  # noqa: BLE001 - a settings problem is not an error here
            stored = ""
        return str(stored or "").strip()
    return ""


def _cache_path(cache_dir: Path | str | None) -> Path | None:
    if cache_dir is None:
        return None
    return Path(cache_dir) / CACHE_NAME


def _load_cache(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_cache(path: Path | None, cache: Mapping[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        pass


def _default_fetch(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "Lumora (ratings)"})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read(MAX_BODY_BYTES).decode("utf-8", "replace"))


def _omdb_url(imdb_id: str, key: str) -> str:
    query = urllib.parse.urlencode({"i": imdb_id, "apikey": key, "tomatoes": "true"})
    return f"{OMDB_URL}?{query}"


def fetch_ratings(
    imdb_id: str,
    tmdb_vote: Any,
    tmdb_votes: Any,
    *,
    key: str = "",
    fetcher: Callable[[str], Any] | None = None,
    cache_dir: Path | str | None = None,
    now: float | None = None,
) -> Ratings:
    """The three scores, using a seven-day cache and never raising.

    TMDB's percentage is computed every time (it is already in hand); IMDb and
    Rotten Tomatoes come from OMDb and are cached per IMDb id.  With no key,
    no id, a failed request or a bad payload, the fields that could not be
    established stay ``None``.
    """
    tmdb = tmdb_percent(tmdb_vote, tmdb_votes)
    clean_id = str(imdb_id or "").strip()
    if not clean_id:
        return Ratings(tmdb_percent=tmdb)

    path = _cache_path(cache_dir)
    cache = _load_cache(path)
    entry = cache.get(clean_id)
    moment = float(now if now is not None else time.time())
    if isinstance(entry, Mapping):
        try:
            stored_at = float(entry.get("at") or 0)
        except (TypeError, ValueError):
            stored_at = 0.0
        if moment - stored_at < CACHE_TTL:
            cached = parse_omdb(entry.get("payload"))
            return Ratings(
                imdb=cached.imdb, rotten=cached.rotten, tmdb_percent=tmdb
            )

    if not str(key or "").strip():
        return Ratings(tmdb_percent=tmdb)

    fetch = fetcher or _default_fetch
    try:
        payload = fetch(_omdb_url(clean_id, key))
    except Exception:  # noqa: BLE001 - a ratings row must never break a page
        return Ratings(tmdb_percent=tmdb)

    fresh = parse_omdb(payload)
    if fresh.imdb is None and fresh.rotten is None:
        # "Movie not found!" or a shape we do not understand: nothing to cache.
        return Ratings(tmdb_percent=tmdb)

    cache[clean_id] = {"at": moment, "payload": payload}
    _write_cache(path, cache)
    return Ratings(imdb=fresh.imdb, rotten=fresh.rotten, tmdb_percent=tmdb)


__all__ = [
    "CACHE_NAME",
    "CACHE_TTL",
    "OMDB_ENV",
    "OMDB_HOST",
    "OMDB_SETTING",
    "OMDB_URL",
    "Ratings",
    "fetch_ratings",
    "parse_omdb",
    "read_omdb_key",
    "tmdb_percent",
]
