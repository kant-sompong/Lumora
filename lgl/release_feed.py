"""Real release dates for the calendar: AniList (anime) and TMDB (TV, movies).

:mod:`lgl.anime_calendar` knows how to group, follow and describe entries but
had nothing to show.  This module fetches them.

* **Anime** — AniList's public GraphQL airing schedule: exact air times, no key.
* **TV** — TMDB ``/tv/on_the_air`` then each show's ``next_episode_to_air``
  (a date, no time of day).  Needs the user's TMDB key.
* **Movies** — TMDB ``/discover/movie`` for the coming weeks (a date).

Everything the network returns is untrusted: bad rows are skipped, and a failed
source becomes a message instead of an exception.  Results are cached on disk so
the calendar opens instantly and still works offline.  Network access is passed
in (``anilist_post``, ``tmdb_get``), which is how the tests run without it.
"""

from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import platform_support, tmdb
from .anime_calendar import Entry, entry_from

ANILIST_URL = "https://graphql.anilist.co"
USER_AGENT = "Lumora (personal desktop client)"
CACHE_TTL = 3 * 3600
MAX_PAGES = 4
MAX_TV_SHOWS = 20

KIND_ANIME = "anime"
KIND_TV = "tv"
KIND_MOVIE = "movie"
KINDS = (KIND_ANIME, KIND_TV, KIND_MOVIE)

_ANILIST_QUERY = """
query ($from: Int, $to: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(airingAt_greater: $from, airingAt_lesser: $to, sort: TIME) {
      airingAt
      episode
      media { id isAdult title { romaji english } }
    }
  }
}
"""


def kind_of(entry: Entry) -> str:
    """``anime``, ``tv`` or ``movie``, read from the entry key."""
    if entry.key.startswith("anilist:"):
        return KIND_ANIME
    if entry.key.startswith("tmdb:movie:"):
        return KIND_MOVIE
    return KIND_TV


# -- transport -------------------------------------------------------------

def anilist_post(body: Mapping[str, Any], *, timeout: float = 15.0) -> Any:
    """POST one GraphQL request to AniList and return the decoded JSON."""
    request = urllib.request.Request(
        ANILIST_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(2 * 1024 * 1024).decode("utf-8", "replace"))


# -- sources ---------------------------------------------------------------

def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def fetch_anime(
    *, now: float, days: int, post: Callable[[Mapping[str, Any]], Any] = anilist_post
) -> list[Entry]:
    """Anime episodes airing between ``now`` and ``days`` ahead (AniList)."""
    start, end = int(now), int(now + days * 86400)
    entries: list[Entry] = []
    for page in range(1, MAX_PAGES + 1):
        payload = post({"query": _ANILIST_QUERY, "variables": {"from": start, "to": end, "page": page}})
        block = (payload or {}).get("data", {}).get("Page") if isinstance(payload, dict) else None
        if not isinstance(block, dict):
            raise ValueError("AniList returned an unexpected document.")
        for row in block.get("airingSchedules") or []:
            if not isinstance(row, dict):
                continue
            media = row.get("media") if isinstance(row.get("media"), dict) else {}
            ident = _int(media.get("id"))
            if ident is None or media.get("isAdult"):
                continue
            titles = media.get("title") if isinstance(media.get("title"), dict) else {}
            title = titles.get("english") or titles.get("romaji") or ""
            entry = entry_from({
                "key": f"anilist:{ident}",
                "title": title,
                "episode": _int(row.get("episode")) or "",
                "air_at": _int(row.get("airingAt")),
                "source": "AniList",
            })
            if entry is not None and entry.known:
                entries.append(entry)
        if not (block.get("pageInfo") or {}).get("hasNextPage"):
            break
    return entries


def _day(value: Any) -> float | None:
    """``YYYY-MM-DD`` to midnight UTC, or ``None`` for anything else."""
    try:
        parsed = datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).timestamp()


def fetch_movies(*, now: float, days: int, get: Callable[[str, Mapping[str, Any]], Any]) -> list[Entry]:
    """Movies releasing in the window (TMDB)."""
    first = datetime.fromtimestamp(now, tz=timezone.utc).date()
    last = first + timedelta(days=days)
    data = get("/discover/movie", {
        "primary_release_date.gte": first.isoformat(),
        "primary_release_date.lte": last.isoformat(),
        "sort_by": "popularity.desc",
        "include_adult": "false",
    })
    entries: list[Entry] = []
    for row in (data or {}).get("results") or []:
        if not isinstance(row, dict):
            continue
        ident, when = _int(row.get("id")), _day(row.get("release_date"))
        entry = entry_from({
            "key": f"tmdb:movie:{ident}" if ident else "",
            "title": row.get("title"),
            "air_at": when,
            "source": "TMDB",
            "date_only": True,
        })
        if ident and entry is not None and entry.known:
            entries.append(entry)
    return entries


def fetch_tv(*, now: float, days: int, get: Callable[[str, Mapping[str, Any]], Any]) -> list[Entry]:
    """Next episode of the shows currently on the air (TMDB)."""
    listing = get("/tv/on_the_air", {"page": 1})
    shows = [r for r in (listing or {}).get("results") or [] if isinstance(r, dict) and _int(r.get("id"))]
    horizon = now + days * 86400

    def one(show: Mapping[str, Any]) -> Entry | None:
        ident = _int(show.get("id"))
        try:
            detail = get(f"/tv/{ident}", {})
        except Exception:  # noqa: BLE001 - one bad show must not sink the rest
            return None
        nxt = detail.get("next_episode_to_air") if isinstance(detail, dict) else None
        if not isinstance(nxt, dict):
            return None
        when = _day(nxt.get("air_date"))
        season, number = _int(nxt.get("season_number")), _int(nxt.get("episode_number"))
        label = f"S{season}E{number}" if season and number else (f"E{number}" if number else "")
        entry = entry_from({
            "key": f"tmdb:tv:{ident}",
            "title": show.get("name"),
            "episode": label,
            "air_at": when,
            "source": "TMDB",
            "date_only": True,
        })
        # TMDB dates are whole days; keep today's episode even though midnight UTC has passed.
        if entry is None or when is None or when < now - 86400 or when > horizon:
            return None
        return entry

    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(one, shows[:MAX_TV_SHOWS]))
    return [e for e in found if e is not None]


def tmdb_getter(settings: Any = None) -> Callable[[str, Mapping[str, Any]], Any] | None:
    """A TMDB reader using the user's key, or ``None`` when no key is set."""
    key = tmdb.load_api_key(settings=settings)
    return tmdb.TmdbClient(key).get if key else None


def default_cache_path() -> Path:
    return platform_support.cache_dir() / "release-calendar.json"


FILTERS = (("all", "Everything"), ("following", "Following"), (KIND_ANIME, "Anime"), (KIND_TV, "TV"), (KIND_MOVIE, "Movies"))


def select(entries: Sequence[Entry], choice: str, followed: Sequence[str] = ()) -> list[Entry]:
    """Entries for one filter choice, soonest first (undated ones last)."""
    chosen = set(followed)
    out = []
    for entry in entries:
        if choice == "following" and entry.key not in chosen:
            continue
        if choice in KINDS and kind_of(entry) != choice:
            continue
        out.append(entry)
    out.sort(key=lambda e: (e.air_at is None, e.air_at or 0.0, e.title.casefold()))
    return out


# -- cache + orchestration -------------------------------------------------

@dataclass
class FeedResult:
    entries: list[Entry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched_at: float = 0.0
    from_cache: bool = False


def _load(path: Path) -> FeedResult | None:
    try:
        data = json.loads(path.read_text("utf-8"))
        entries = [Entry(**row) for row in data["entries"] if isinstance(row, dict)]
        return FeedResult(entries, list(data.get("errors", [])), float(data["fetched_at"]), True)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save(path: Path, result: FeedResult) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "fetched_at": result.fetched_at,
            "errors": result.errors,
            "entries": [asdict(e) for e in result.entries],
        }), "utf-8")
        tmp.replace(path)
    except OSError:
        pass  # a cache that cannot be written is not an error


def load_feed(
    cache_path: Path,
    *,
    now: float | None = None,
    days: int = 14,
    force: bool = False,
    tmdb_get: Callable[[str, Mapping[str, Any]], Any] | None = None,
    anilist: Callable[[Mapping[str, Any]], Any] = anilist_post,
    ttl: float = CACHE_TTL,
) -> FeedResult:
    """The calendar entries: from the cache when fresh, otherwise fetched.

    Each source fails on its own.  If every source fails the last good cache is
    returned (marked ``from_cache``) with the error messages attached.
    """
    current = time.time() if now is None else float(now)
    cached = _load(cache_path)
    if cached and not force and 0 <= current - cached.fetched_at < ttl:
        return cached

    entries: list[Entry] = []
    errors: list[str] = []
    jobs: list[tuple[str, Callable[[], list[Entry]]]] = [
        ("anime (AniList)", lambda: fetch_anime(now=current, days=days, post=anilist)),
    ]
    if tmdb_get is not None:
        jobs.append(("TV (TMDB)", lambda: fetch_tv(now=current, days=days, get=tmdb_get)))
        jobs.append(("movies (TMDB)", lambda: fetch_movies(now=current, days=days, get=tmdb_get)))
    else:
        errors.append("TV and movies need a TMDB key (Settings → Media).")
    ok = 0
    for label, job in jobs:
        try:
            entries.extend(job())
            ok += 1
        except Exception as exc:  # noqa: BLE001 - offline is normal
            errors.append(f"Could not load {label}: {exc}")
    if ok == 0 and cached:
        return FeedResult(cached.entries, errors, cached.fetched_at, True)
    result = FeedResult(entries, errors, current, False)
    if ok:
        _save(cache_path, result)
    return result


__all__ = [
    "CACHE_TTL", "FeedResult", "KINDS", "KIND_ANIME", "KIND_MOVIE", "KIND_TV",
    "FILTERS", "anilist_post", "default_cache_path", "fetch_anime", "fetch_movies", "fetch_tv", "kind_of", "load_feed", "select", "tmdb_getter",
]
