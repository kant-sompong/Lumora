"""Anime hidden gems: high scores on titles most people have not watched yet.

A hidden gem needs both a score *and* a popularity number.  Only AniList gives
us that pair here, so this module looks **only at anime**: the local game
catalogue and TMDB movies/series have no comparable score/popularity fields and
are deliberately not covered.

The rule is plain: an anime is a gem when its score is at least ``min_score``
(78 by default), its popularity is below the cutoff (the median popularity of
the fetched titles by default), and it is not already in the user's library or
the calendar's followed list.  Gems come back sorted by score.

Fetching mirrors :mod:`lgl.season_data`: one GraphQL query of its own (the
season query is untouched), an injectable HTTP call, a three-hour JSON cache
beside the release feed's cache, and one plain sentence for any failure.  No
GUI toolkit is imported here, so both frontends share the same logic.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import platform_support, release_feed, season_data

#: Three hours, the same freshness as the season list and release calendar.
CACHE_TTL = season_data.CACHE_TTL
CACHE_FILENAME = "hidden-gems.json"
#: v2 added poster_url; a v1 cache has no cover art, so it is ignored.
CACHE_VERSION = 2
LISTS_CACHE_FILENAME = "anime-lists.json"
#: A score of 78 is "good, not just fine" on AniList's 100-point average.
MIN_SCORE = 78
#: Highest value the popularity control accepts; 0 means "the fetched median".
MAX_POPULARITY = 2_000_000
MAX_PAGES = 3
PER_PAGE = 50

#: AniList's popularity-ordered anime list.  ``popularity`` (how many users have
#: the title on a list) is what makes a gem "hidden"; ``averageScore`` says it is
#: still good.
QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
      id
      title { romaji english }
      averageScore
      popularity
      coverImage { large }
      episodes
      format
      status
    }
  }
}
"""

Post = Callable[[Mapping[str, Any]], Any]


# -- thresholds ------------------------------------------------------------

def clean_score(value: Any) -> int:
    """A score threshold clamped to AniList's 0-100 range; 78 when unreadable."""
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return MIN_SCORE
    return max(0, min(100, score))


def clean_popularity(value: Any) -> int:
    """A popularity cutoff; 0 (or anything unreadable) means "use the median"."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_POPULARITY, number))


def median_popularity(entries: Sequence[Mapping[str, Any]]) -> int:
    """The middle popularity of the fetched titles, rounded to a whole number."""
    values = [
        int(entry["popularity"])
        for entry in entries
        if isinstance(entry.get("popularity"), int)
    ]
    if not values:
        return 0
    return int(round(statistics.median(values)))


def _text_set(values: Iterable[Any]) -> set[str]:
    return {
        " ".join(str(value or "").split()).casefold()
        for value in values
        if str(value or "").strip()
    }


def is_excluded(entry: Mapping[str, Any], excluded: set[str]) -> bool:
    """True when the library or the followed list already names this title."""
    if not excluded:
        return False
    key = str(entry.get("key") or "").strip().casefold()
    title = str(entry.get("title") or "").strip().casefold()
    if key and key in excluded:
        return True
    if title and title in excluded:
        return True
    ident = key.split(":", 1)[-1]
    return bool(ident) and ident in excluded


def select(
    entries: Sequence[Mapping[str, Any]],
    *,
    min_score: Any = MIN_SCORE,
    max_popularity: Any = 0,
    excluded: Iterable[Any] = (),
) -> tuple[list[dict[str, Any]], int]:
    """``(gems, cutoff)``: what passes both thresholds, best score first.

    The cutoff is ``max_popularity`` when it is a positive number, otherwise the
    median popularity of ``entries`` (so a fetched page always has a middle).
    "Below the cutoff" is strict: a title sitting exactly on it is not a gem.
    """
    floor = clean_score(min_score)
    ceiling = clean_popularity(max_popularity) or median_popularity(entries)
    blocked = _text_set(excluded)
    gems: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        score = entry.get("score")
        popularity = entry.get("popularity")
        if not isinstance(score, int) or not isinstance(popularity, int):
            continue
        if score < floor or popularity >= ceiling:
            continue
        if is_excluded(entry, blocked):
            continue
        gems.append(dict(entry))
    gems.sort(
        key=lambda row: (
            -int(row.get("score") or 0),
            int(row.get("popularity") or 0),
            str(row.get("title") or "").casefold(),
        )
    )
    return gems, ceiling


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
    data = payload.get("data")
    block = data.get("Page") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        raise ValueError("AniList returned no anime page.")
    return block


def fetch_catalogue(*, post: Post | None = None) -> list[dict[str, Any]]:
    """Up to three popularity-ordered pages of anime, each with its rank."""
    sender = release_feed.anilist_post if post is None else post
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        payload = sender({"query": QUERY, "variables": {"page": page}})
        block = _page_block(payload)
        rows = block.get("media")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            ident = _int(row.get("id"), minimum=1)
            title = _title(row)
            score = _int(row.get("averageScore"))
            popularity = _int(row.get("popularity"))
            if ident is None or not title or popularity is None:
                continue
            cover = row.get("coverImage") if isinstance(row.get("coverImage"), dict) else {}
            poster = str(cover.get("large") or cover.get("extraLarge") or "").strip()
            key = f"anilist:{ident}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "key": key,
                    "title": title,
                    "score": score,
                    "popularity": popularity,
                    "rank": len(entries) + 1,
                    "episodes": _int(row.get("episodes"), minimum=1),
                    "format": str(row.get("format") or ""),
                    "status": str(row.get("status") or ""),
                    "poster_url": poster,
                    "source": "AniList",
                }
            )
        info = block.get("pageInfo")
        if not isinstance(info, dict) or not info.get("hasNextPage"):
            break
    return entries


# -- cache -----------------------------------------------------------------

#: Bump when a cached list would be missing a field the rows now use.
LISTS_VERSION = 2

LISTS_QUERY = """
query ($sort: [MediaSort]) {
  Page(page: 1, perPage: 50) {
    media(type: ANIME, sort: $sort, isAdult: false) {
      id
      title { romaji english }
      format
      status
      episodes
      endDate { year month day }
      coverImage { large }
    }
  }
}
"""


def _list_rows(block: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = (block or {}).get("data", {}).get("Page") if isinstance(block, dict) else None
    if not isinstance(page, dict):
        return rows
    for raw in page.get("media") or []:
        if not isinstance(raw, dict):
            continue
        titles = raw.get("title") if isinstance(raw.get("title"), dict) else {}
        title = str(titles.get("english") or titles.get("romaji") or "").strip()
        if not title:
            continue
        cover = raw.get("coverImage") if isinstance(raw.get("coverImage"), dict) else {}
        end = raw.get("endDate") if isinstance(raw.get("endDate"), dict) else {}
        end_text = ""
        if end.get("year") and end.get("month") and end.get("day"):
            end_text = f"{int(end['year']):04d}-{int(end['month']):02d}-{int(end['day']):02d}"
        rows.append({
            "id": _int(raw.get("id")) or 0,
            "title": title,
            "format": str(raw.get("format") or ""),
            "status": str(raw.get("status") or ""),
            "episodes": _int(raw.get("episodes")) or 0,
            "end_date": end_text,
            "poster_url": str(
                (cover.get("large") if isinstance(cover, dict) else "") or ""
            ),
        })
    return rows


def load_anime_lists(
    *,
    post: Post | None = None,
    now: float | None = None,
    ttl: float = CACHE_TTL,
    cache_path: Path | str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """New Added and Just Completed anime, from AniList, cached for three hours.

    A read-only companion to :func:`load_gems`: same transport, own cache file,
    and one plain sentence per source instead of an exception.
    """
    path = Path(cache_path) if cache_path is not None else platform_support.cache_dir() / LISTS_CACHE_FILENAME
    current = time.time() if now is None else float(now)
    cached: dict[str, Any] = {}
    try:
        cached = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        cached = {}
    if (
        not force
        and isinstance(cached, dict)
        and isinstance(cached.get("fetched_at"), (int, float))
        and 0 <= current - float(cached["fetched_at"]) < ttl
        and isinstance(cached.get("new_added"), list)
        and cached.get("version") == LISTS_VERSION
    ):
        return {
            "new_added": list(cached.get("new_added") or []),
            "just_completed": list(cached.get("just_completed") or []),
            "errors": [],
            "from_cache": True,
        }
    call = post or release_feed.anilist_post
    added: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        added = _list_rows(call({"query": LISTS_QUERY, "variables": {"sort": ["ID_DESC"]}}))
    except Exception as exc:  # noqa: BLE001 - shown as one line
        errors.append(f"New Added: {exc}")
    try:
        completed = _list_rows(
            call({"query": LISTS_QUERY, "variables": {"sort": ["END_DATE_DESC"]}})
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Just Completed: {exc}")
    if errors and isinstance(cached.get("new_added"), list):
        return {
            "new_added": list(cached.get("new_added") or []),
            "just_completed": list(cached.get("just_completed") or []),
            "errors": errors,
            "from_cache": True,
        }
    block = {
        "version": LISTS_VERSION,
        "fetched_at": current,
        "new_added": added,
        "just_completed": completed,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(block), "utf-8")
        tmp.replace(path)
    except OSError:
        pass
    return {"new_added": added, "just_completed": completed, "errors": errors, "from_cache": False}


def default_cache_path() -> Path:
    """Beside the release calendar's cache, like the season list."""
    return platform_support.cache_dir() / CACHE_FILENAME


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") != CACHE_VERSION:
        return {}
    block = data.get("catalogue")
    return block if isinstance(block, dict) else {}


def _save_cache(path: Path, block: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"version": CACHE_VERSION, "catalogue": dict(block)}, indent=2,
                       sort_keys=True),
            "utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass  # a cache that cannot be written is not an error


def _entries_of(block: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = block.get("entries")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _stamp_of(block: Mapping[str, Any]) -> float:
    try:
        return float(block.get("fetched_at"))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class GemsResult:
    """The gems for the current thresholds, or the reason there are none."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    from_cache: bool = False
    fetched_at: float = 0.0
    scanned: int = 0
    median: int = 0
    min_score: int = MIN_SCORE
    max_popularity: int = 0

    @property
    def cutoff(self) -> int:
        return self.max_popularity or self.median

    @property
    def error_text(self) -> str:
        """``Could not load: …`` for the window, or an empty string."""
        return f"Could not load: {self.error}" if self.error else ""

    def note(self) -> str:
        """One sentence under the list: how many passed, or what to try."""
        if self.error and not self.entries:
            return ""
        if not self.entries:
            return (
                f"Nothing scores {self.min_score}+ below popularity {self.cutoff:,} "
                f"of the {self.scanned} titles fetched. Try lowering the score."
            )
        text = (
            f"{len(self.entries)} of {self.scanned} titles score {self.min_score}+ "
            f"with popularity under {self.cutoff:,}."
        )
        if self.from_cache:
            text += " Showing saved titles."
        return text


def load_gems(
    *,
    post: Post | None = None,
    now: float | None = None,
    ttl: float = CACHE_TTL,
    cache_path: Path | str | None = None,
    min_score: Any = MIN_SCORE,
    max_popularity: Any = 0,
    excluded: Iterable[Any] = (),
    force: bool = False,
) -> GemsResult:
    """The fetched catalogue filtered by both thresholds and the user's lists.

    A fresh cache answers for three hours; a stale one is still shown when the
    network fails, with the failure attached so the window can say so.
    """
    path = Path(cache_path) if cache_path is not None else default_cache_path()
    current = time.time() if now is None else float(now)
    block = _load_cache(path)
    entries = _entries_of(block)
    stamp = _stamp_of(block)
    fresh = bool(entries) and 0 <= current - stamp < ttl

    if fresh and not force:
        gems, cutoff = select(
            entries,
            min_score=min_score,
            max_popularity=max_popularity,
            excluded=excluded,
        )
        return GemsResult(
            entries=gems,
            from_cache=True,
            fetched_at=stamp,
            scanned=len(entries),
            median=median_popularity(entries),
            min_score=clean_score(min_score),
            max_popularity=clean_popularity(max_popularity),
        )

    error = ""
    try:
        entries = fetch_catalogue(post=post)
    except Exception as exc:  # noqa: BLE001 - offline is normal for this window
        error = f"AniList: {season_data.reason_of(exc)}"
        entries = _entries_of(block)
    if not entries and not error:
        error = "AniList returned no anime."

    if not entries:
        return GemsResult(
            error=error,
            min_score=clean_score(min_score),
            max_popularity=clean_popularity(max_popularity),
        )

    stamp = current if not error else stamp
    if not error:
        _save_cache(path, {"fetched_at": current, "entries": entries})
    gems, cutoff = select(
        entries,
        min_score=min_score,
        max_popularity=max_popularity,
        excluded=excluded,
    )
    return GemsResult(
        entries=gems,
        error=error,
        from_cache=bool(error),
        fetched_at=stamp,
        scanned=len(entries),
        median=median_popularity(entries),
        min_score=clean_score(min_score),
        max_popularity=clean_popularity(max_popularity),
    )


# -- drawing helpers (pure) ------------------------------------------------

def format_popularity(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "unknown"


def entry_line(entry: Mapping[str, Any], scanned: int = 0) -> str:
    """One list row: title, score, then where it sits in the fetched popularity."""
    title = str(entry.get("title") or entry.get("key") or "Untitled").strip()
    score = entry.get("score")
    bits = [f"score {score}" if isinstance(score, int) else "score unknown"]
    rank = entry.get("rank")
    if isinstance(rank, int) and rank > 0:
        bits.append(
            f"popularity rank {rank} of {scanned}" if scanned else f"popularity rank {rank}"
        )
    else:
        bits.append(f"popularity {format_popularity(entry.get('popularity'))}")
    return f"{title} — {' · '.join(bits)}"


def entry_details(entry: Mapping[str, Any] | None, scanned: int = 0) -> str:
    """The line under the list for the selected title."""
    if not entry:
        return ""
    score = entry.get("score")
    bits = [f"Score {score}/100" if isinstance(score, int) else "Score unknown"]
    popularity = format_popularity(entry.get("popularity"))
    rank = entry.get("rank")
    if isinstance(rank, int) and rank > 0:
        bits.append(f"popularity {popularity} (rank {rank} of {scanned})")
    else:
        bits.append(f"popularity {popularity}")
    episodes = entry.get("episodes")
    if isinstance(episodes, int) and episodes:
        bits.append(f"{episodes} episode{'s' if episodes != 1 else ''}")
    fmt = season_data.format_name(entry)
    if fmt:
        bits.append(fmt)
    status = str(entry.get("status") or "").strip().replace("_", " ").title()
    if status:
        bits.append(status)
    return " · ".join(bits)


__all__ = [
    "CACHE_FILENAME",
    "CACHE_TTL",
    "GemsResult",
    "MAX_PAGES",
    "MAX_POPULARITY",
    "MIN_SCORE",
    "QUERY",
    "clean_popularity",
    "clean_score",
    "default_cache_path",
    "entry_details",
    "entry_line",
    "fetch_catalogue",
    "format_popularity",
    "is_excluded",
    "load_anime_lists",
    "load_gems",
    "median_popularity",
    "select",
]
