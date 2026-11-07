"""AniList titles for one anime season, cached beside the release feed.

:mod:`lgl.season_compare` compares two lists, but nothing fetched those lists,
so the comparison window opened with empty pickers.  This module fills that gap:
it asks AniList for one season's anime, keeps the answer for three hours in a
JSON file next to the release calendar's cache, and turns every failure into a
plain sentence instead of an exception.

The HTTP call is passed in (:func:`lgl.release_feed.anilist_post` by default), so
tests run without the network.  Nothing here imports a GUI toolkit.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import platform_support, release_feed, season_compare

SEASONS = ("WINTER", "SPRING", "SUMMER", "FALL")
SEASON_NAMES = {
    "WINTER": "Winter",
    "SPRING": "Spring",
    "SUMMER": "Summer",
    "FALL": "Fall",
}
#: Three hours, the same freshness the release calendar uses.
CACHE_TTL = 3 * 3600
CACHE_FILENAME = "season-compare.json"
MAX_PAGES = 4
PER_PAGE = 50
MIN_YEAR = 1940
MAX_YEAR = 2100
MAX_SEASONS_IN_CACHE = 12

#: The season list AniList's ``Page.media`` works from.  ``sort`` is popularity,
#: so a big season shows the titles people actually know first.
QUERY = """
query ($season: MediaSeason, $seasonYear: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(season: $season, seasonYear: $seasonYear, type: ANIME, sort: POPULARITY_DESC) {
      id
      title { romaji english }
      averageScore
      episodes
      format
      status
    }
  }
}
"""

Post = Callable[[Mapping[str, Any]], Any]


# -- seasons ---------------------------------------------------------------

def clean_season(value: Any) -> str:
    """``spring``/``SPRING``/``Spring`` to ``SPRING``; anything else to ``""``."""
    text = str(value or "").strip().upper()
    return text if text in SEASONS else ""


def clean_year(value: Any) -> int | None:
    """A sane four-digit year, or ``None``."""
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if MIN_YEAR <= year <= MAX_YEAR else None


def season_key(season: Any, year: Any) -> str:
    """``2024 spring``, the key :mod:`lgl.season_compare` groups by."""
    name = clean_season(season)
    number = clean_year(year)
    if not name or number is None:
        return ""
    return f"{number} {name.casefold()}"


def season_label(season: Any, year: Any) -> str:
    """``Spring 2024`` for headings and messages."""
    name = clean_season(season)
    number = clean_year(year)
    if not name or number is None:
        return "an unknown season"
    return f"{SEASON_NAMES[name]} {number}"


def latest_season(now: float | None = None) -> tuple[str, int]:
    """The season the current date is in, as ``(SEASON, year)``."""
    stamp = time.time() if now is None else float(now)
    month = time.gmtime(stamp).tm_mon
    year = time.gmtime(stamp).tm_year
    index = (month - 1) // 3
    return SEASONS[index], year


def previous_season(season: Any, year: Any) -> tuple[str, int]:
    """The season before ``season``/``year``."""
    name = clean_season(season)
    number = clean_year(year)
    if not name or number is None:
        return latest_season()
    index = SEASONS.index(name)
    if index == 0:
        return SEASONS[-1], max(MIN_YEAR, number - 1)
    return SEASONS[index - 1], number


# -- fetching --------------------------------------------------------------

def _int(value: Any, *, minimum: int = 0) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= minimum else None


def _title(row: Mapping[str, Any]) -> str:
    titles = row.get("title") if isinstance(row.get("title"), dict) else {}
    for raw in (titles.get("english"), titles.get("romaji")):
        text = " ".join(str(raw or "").split())[:200]
        if text:
            return text
    return ""


def _page_block(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("AniList returned an unexpected document.")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        raise ValueError(str(first.get("message") or "AniList reported an error"))
    block = (payload.get("data") or {}).get("Page") if isinstance(payload.get("data"), dict) else None
    if not isinstance(block, dict):
        raise ValueError("AniList returned no season page.")
    return block


def fetch_season(
    season: Any,
    year: Any,
    *,
    post: Post | None = None,
) -> list[dict[str, Any]]:
    """Every anime AniList lists for one season, newest page merged in order."""
    name = clean_season(season)
    number = clean_year(year)
    if not name:
        raise ValueError(f"Unknown season {season!r}")
    if number is None:
        raise ValueError(f"Unknown year {year!r}")
    sender = release_feed.anilist_post if post is None else post
    key = season_key(name, number)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        payload = sender(
            {
                "query": QUERY,
                "variables": {"season": name, "seasonYear": number, "page": page},
            }
        )
        block = _page_block(payload)
        rows = block.get("media")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            ident = _int(row.get("id"), minimum=1)
            title = _title(row)
            if ident is None or not title:
                continue
            entry_key = f"anilist:{ident}"
            if entry_key in seen:
                continue
            seen.add(entry_key)
            entries.append(
                {
                    "key": entry_key,
                    "title": title,
                    "season": name.casefold(),
                    "year": number,
                    "season_key": key,
                    "score": _int(row.get("averageScore")),
                    "episodes": _int(row.get("episodes"), minimum=1),
                    "format": str(row.get("format") or ""),
                    "status": str(row.get("status") or ""),
                    "source": "AniList",
                }
            )
        info = block.get("pageInfo")
        if not isinstance(info, dict) or not info.get("hasNextPage"):
            break
    return entries


# -- cache -----------------------------------------------------------------

def default_cache_path() -> Path:
    """Beside the release calendar's cache, so both live in ``cache_dir()``."""
    return platform_support.cache_dir() / CACHE_FILENAME


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    seasons = data.get("seasons")
    return seasons if isinstance(seasons, dict) else {}


def _save_cache(path: Path, seasons: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "seasons": dict(seasons)}, indent=2, sort_keys=True),
            "utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass  # a cache that cannot be written is not an error


def _cached(seasons: Mapping[str, Any], key: str) -> "SeasonResult | None":
    record = seasons.get(key)
    if not isinstance(record, dict):
        return None
    items = record.get("entries")
    fetched = record.get("fetched_at")
    if not isinstance(items, list):
        return None
    try:
        stamp = float(fetched)
    except (TypeError, ValueError):
        return None
    entries = [dict(row) for row in items if isinstance(row, dict)]
    return SeasonResult(
        season=key.split()[-1].upper() if key else "",
        year=int(key.split()[0]) if key.split()[:1] and key.split()[0].isdigit() else 0,
        entries=entries,
        from_cache=True,
        fetched_at=stamp,
    )


def reason_of(exc: BaseException) -> str:
    """One plain sentence for any failure, never a traceback."""
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "the request timed out"
    if isinstance(exc, urllib.error.URLError):
        return f"network error ({getattr(exc, 'reason', exc)})"
    text = " ".join(str(exc).split())
    return text or exc.__class__.__name__


@dataclass
class SeasonResult:
    """One season's titles, or the reason they could not be loaded."""

    season: str = ""
    year: int = 0
    entries: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    from_cache: bool = False
    fetched_at: float = 0.0

    @property
    def label(self) -> str:
        return season_label(self.season, self.year)


def load_season(
    season: Any,
    year: Any,
    *,
    post: Post | None = None,
    now: float | None = None,
    ttl: float = CACHE_TTL,
    cache_path: Path | str | None = None,
    force: bool = False,
) -> SeasonResult:
    """One season from a fresh cache, or from AniList, or the reason why not.

    A stale cache is still better than nothing when the network is down: it is
    returned with ``from_cache`` set and the failure attached as ``error``.
    """
    name = clean_season(season)
    number = clean_year(year)
    if not name or number is None:
        return SeasonResult(
            error=f"Unknown season {season!r} / year {year!r}",
        )
    path = Path(cache_path) if cache_path is not None else default_cache_path()
    current = time.time() if now is None else float(now)
    key = season_key(name, number)
    seasons = _load_cache(path)
    cached = _cached(seasons, key)
    if cached is not None and not force and 0 <= current - cached.fetched_at < ttl:
        cached.season = name
        cached.year = number
        return cached

    try:
        entries = fetch_season(name, number, post=post)
    except Exception as exc:  # noqa: BLE001 - offline is normal for this window
        message = f"AniList ({season_label(name, number)}): {reason_of(exc)}"
        if cached is not None:
            cached.season = name
            cached.year = number
            cached.error = message
            return cached
        return SeasonResult(season=name, year=number, error=message)

    result = SeasonResult(
        season=name, year=number, entries=entries, fetched_at=current
    )
    seasons[key] = {"fetched_at": current, "entries": entries}
    if len(seasons) > MAX_SEASONS_IN_CACHE:
        ordered = sorted(
            seasons.items(), key=lambda item: float(item[1].get("fetched_at") or 0.0)
        )
        seasons = dict(ordered[-MAX_SEASONS_IN_CACHE:])
    _save_cache(path, seasons)
    return result


# -- comparing -------------------------------------------------------------

@dataclass
class CompareResult:
    """What both windows draw: three lists, a summary, and any failure text."""

    first_label: str = ""
    second_label: str = ""
    first_key: str = ""
    second_key: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    summary: str = "Nothing to compare"
    errors: list[str] = field(default_factory=list)
    left: SeasonResult | None = None
    right: SeasonResult | None = None

    @property
    def only_first(self) -> list[Mapping[str, Any]]:
        return list(self.result.get("only_first") or [])

    @property
    def only_second(self) -> list[Mapping[str, Any]]:
        return list(self.result.get("only_second") or [])

    @property
    def shared(self) -> list[Mapping[str, Any]]:
        return list(self.result.get("shared") or [])

    @property
    def error_text(self) -> str:
        """``Could not load: …`` for the window, or an empty string."""
        if not self.errors:
            return ""
        return "Could not load: " + "; ".join(self.errors)

    @property
    def saved_titles(self) -> bool:
        return bool((self.left and self.left.from_cache) or (self.right and self.right.from_cache))


def failed_compare(reason: Any) -> CompareResult:
    """A result that says why nothing could be compared."""
    text = " ".join(str(reason or "").split()) or "the comparison failed"
    return CompareResult(errors=[text])


def compare(
    season_a: Any,
    year_a: Any,
    season_b: Any,
    year_b: Any,
    *,
    post: Post | None = None,
    now: float | None = None,
    ttl: float = CACHE_TTL,
    cache_path: Path | str | None = None,
    force: bool = False,
) -> CompareResult:
    """Load both seasons and compare them by title key."""
    left = load_season(
        season_a, year_a, post=post, now=now, ttl=ttl, cache_path=cache_path, force=force
    )
    right = load_season(
        season_b, year_b, post=post, now=now, ttl=ttl, cache_path=cache_path, force=force
    )
    first_key = season_key(season_a, year_a)
    second_key = season_key(season_b, year_b)
    if first_key and second_key:
        comparison = season_compare.compare(
            list(left.entries) + list(right.entries), first_key, second_key
        )
    else:
        comparison = {
            "first": first_key,
            "second": second_key,
            "shared": [],
            "only_first": [],
            "only_second": [],
            "counts": {},
        }
    errors = [message for message in (left.error, right.error) if message]
    summary = season_compare.summary(comparison)
    if errors and not left.entries and not right.entries:
        # Nothing loaded on either side: say so instead of "0 titles".
        summary = "Nothing to compare"
    return CompareResult(
        first_label=season_label(season_a, year_a),
        second_label=season_label(season_b, year_b),
        first_key=first_key,
        second_key=second_key,
        result=comparison,
        summary=summary,
        errors=errors,
        left=left,
        right=right,
    )


# -- drawing helpers (pure) ------------------------------------------------

_FORMATS = {
    "TV": "TV",
    "TV_SHORT": "TV short",
    "OVA": "OVA",
    "ONA": "ONA",
    "MOVIE": "Movie",
    "SPECIAL": "Special",
    "MUSIC": "Music",
}


def format_name(entry: Mapping[str, Any]) -> str:
    raw = str(entry.get("format") or "").strip().upper()
    return _FORMATS.get(raw, raw.replace("_", " ").title() if raw else "")


def entry_line(entry: Mapping[str, Any]) -> str:
    """One list row: the title, then score/format when known."""
    title = str(entry.get("title") or entry.get("key") or "Untitled").strip()
    bits: list[str] = []
    score = _int(entry.get("score"))
    if score is not None:
        bits.append(f"score {score}")
    fmt = format_name(entry)
    if fmt:
        bits.append(fmt)
    return f"{title} — {' · '.join(bits)}" if bits else title


def entry_details(entry: Mapping[str, Any] | None) -> str:
    """The line under the lists for the selected title."""
    if not entry:
        return ""
    bits: list[str] = []
    score = _int(entry.get("score"))
    bits.append(f"Score {score}/100" if score is not None else "Score unknown")
    episodes = _int(entry.get("episodes"))
    bits.append(f"{episodes} episode{'s' if episodes != 1 else ''}" if episodes else "Episode count unknown")
    fmt = format_name(entry)
    if fmt:
        bits.append(fmt)
    status = str(entry.get("status") or "").strip().replace("_", " ").title()
    if status:
        bits.append(status)
    season = season_label(entry.get("season"), entry.get("year"))
    if season != "an unknown season":
        bits.append(season)
    return " · ".join(bits)


__all__ = [
    "CACHE_FILENAME",
    "CACHE_TTL",
    "CompareResult",
    "MAX_PAGES",
    "MIN_YEAR",
    "QUERY",
    "SEASONS",
    "SEASON_NAMES",
    "SeasonResult",
    "clean_season",
    "clean_year",
    "compare",
    "default_cache_path",
    "entry_details",
    "entry_line",
    "failed_compare",
    "fetch_season",
    "format_name",
    "latest_season",
    "load_season",
    "previous_season",
    "reason_of",
    "season_key",
    "season_label",
]
