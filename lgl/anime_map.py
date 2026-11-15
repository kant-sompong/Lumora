"""Which AniList entry plays the TMDB title on screen.

TMDB is the catalogue and ani.pm is the player, and ani.pm plays by AniList id:
``/embed/ani/<anilist_id>/<episode>/<sub|dub>``.  This module is the bridge
between the two, built on the public Fribb/anime-lists mapping list.

One record in that list is one *AniList entry*, not one series: a TMDB season
can be split across several of them.  Attack on Titan's third season is two
entries on AniList (the first 12 episodes and "Part 2"), both pointing at TMDB
season 3, the second carrying ``episode_offset.tmdb = 12``.  The offset is
what this module uses to turn a TMDB episode into the entry and the episode
number that entry knows: TMDB episode 15 is AniList season 3 part 2, episode 3.

Nothing here imports GTK or Qt, and nothing guesses.  A season whose entries
cannot be told apart comes back ``confident=False`` with the candidates, so the
page can ask which part instead of playing the wrong one; a title that cannot
be linked is ``None``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import platform_support, streaming

__all__ = [
    "CACHE_FILENAME",
    "LIST_URL",
    "WEEK",
    "MappingList",
    "PlayTarget",
    "anilist_to_tmdb",
    "default_cache_path",
    "load_mapping",
    "tmdb_to_anilist",
]

#: The public mapping list: one JSON record per AniList entry.
LIST_URL = (
    "https://raw.githubusercontent.com/Fribb/anime-lists/master/"
    "anime-list-full.json"
)
#: Kept beside the release calendar and the season caches, in ``cache_dir()``.
CACHE_FILENAME = "anime-list.json"
#: The list changes when new seasons are added to it, so a week is plenty.
WEEK = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class PlayTarget:
    """The AniList entry, and the episode of it, that a TMDB episode plays.

    ``confident`` is false when the mapping could not tell one entry from
    another: ``candidates`` then holds the possibilities (each one numbered for
    its own entry) and the caller should ask instead of playing.
    """

    anilist_id: int = 0
    episode: int = 1
    confident: bool = False
    reason: str = ""
    candidates: tuple["PlayTarget", ...] = ()


@dataclass(frozen=True)
class MappingList:
    """The mapping records, where they came from, and why a refresh failed."""

    records: tuple[Mapping[str, Any], ...] = ()
    fetched_at: float = 0.0
    from_cache: bool = False
    error: str = ""


# -- cache ------------------------------------------------------------------


def default_cache_path() -> Path:
    """The mapping list's cache, beside the other caches."""
    return platform_support.cache_dir() / CACHE_FILENAME


def _backup_path(path: Path) -> Path:
    """The copy that was good until the last successful refresh."""
    return path.with_name(path.name + ".bak")


def _read(path: Path) -> tuple[list[dict[str, Any]], float] | None:
    """The records and their fetch time, or ``None`` for missing or corrupt."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    records = data.get("records")
    fetched_at = data.get("fetched_at")
    if not isinstance(records, list) or not isinstance(fetched_at, (int, float)):
        return None
    if isinstance(fetched_at, bool) or not records:
        return None
    kept = [record for record in records if isinstance(record, dict)]
    return (kept, float(fetched_at)) if kept else None


def _load(path: Path) -> tuple[list[dict[str, Any]], float] | None:
    """The cache, falling back to the last good copy when it is unreadable."""
    for candidate in (path, _backup_path(path)):
        found = _read(candidate)
        if found is not None:
            return found
    return None


def _write(path: Path, records: Sequence[Mapping[str, Any]], fetched_at: float) -> None:
    """Write the cache atomically, keeping the old file as the fallback copy."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"version": 1, "fetched_at": fetched_at, "records": list(records)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "utf-8",
        )
        if path.exists():
            try:
                path.replace(_backup_path(path))
            except OSError:
                pass  # a missing fallback copy is not a reason to fail the write
        tmp.replace(path)
    except OSError:
        pass  # a cache that cannot be written is not an error


def _fetch(timeout: float = 60.0) -> list[dict[str, Any]]:
    """Download the list; raises when the network or the payload fails."""
    import urllib.request

    request = urllib.request.Request(
        LIST_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "Lumora (personal desktop client)",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(64 * 1024 * 1024).decode("utf-8", "replace"))
    if not isinstance(payload, list):
        raise ValueError("the mapping list is not a list")
    records = [record for record in payload if isinstance(record, dict)]
    if not records:
        raise ValueError("the mapping list is empty")
    return records


def load_mapping(
    *,
    cache_path: Path | str | None = None,
    force: bool = False,
    now: float | None = None,
    ttl: float = WEEK,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> MappingList:
    """The mapping records: the fresh cache, a refresh, or the last good copy.

    The cache is refreshed once a week.  A refresh that fails (no network, a
    502, a truncated payload) never throws the cache away: the stale records
    come back with ``from_cache`` set and the failure in ``error``.  With no
    cache at all the result is empty, which every lookup here reads as "no
    link known" rather than as an error to show.
    """
    path = Path(cache_path) if cache_path is not None else default_cache_path()
    current = time.time() if now is None else float(now)
    cached = _load(path)
    if cached is not None and not force and 0 <= current - cached[1] < ttl:
        return MappingList(tuple(cached[0]), cached[1], True)
    reader = fetch or _fetch
    try:
        records = reader()
    except Exception as error:  # noqa: BLE001 - any failure falls back to the cache
        if cached is not None:
            return MappingList(
                tuple(cached[0]), cached[1], True, f"refresh failed: {error}"
            )
        return MappingList((), 0.0, False, f"mapping list unavailable: {error}")
    kept = [record for record in records if isinstance(record, dict)] if isinstance(
        records, list
    ) else []
    if not kept:
        if cached is not None:
            return MappingList(tuple(cached[0]), cached[1], True, "empty refresh")
        return MappingList((), 0.0, False, "mapping list unavailable: empty")
    _write(path, kept, current)
    return MappingList(tuple(kept), current, False)


# -- records ----------------------------------------------------------------


def _number(value: object) -> int | None:
    """A positive JSON integer, or ``None`` (bools are not ids)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _anilist_id(record: Mapping[str, Any]) -> int | None:
    return _number(record.get("anilist_id"))


def _tmdb_ids(record: Mapping[str, Any], kind: str) -> list[int]:
    """Every TMDB id of *kind* this record points at.

    ``themoviedb_id`` is a dict (``{"tv": 1429}``, ``{"movie": [378064]}``); a
    bare integer is tolerated and read through the record's ``type``.
    """
    raw = record.get("themoviedb_id")
    if isinstance(raw, dict):
        value = raw.get(kind)
    elif isinstance(raw, int) and not isinstance(raw, bool):
        record_kind = "movie" if str(record.get("type", "")).upper() == "MOVIE" else "tv"
        value = raw if record_kind == kind else None
    else:
        value = None
    if isinstance(value, list):
        return [number for number in (_number(item) for item in value) if number]
    number = _number(value)
    return [number] if number else []


def _season(record: Mapping[str, Any]) -> int | None:
    """The TMDB season this entry belongs to, when the list says so."""
    raw = record.get("season")
    if not isinstance(raw, dict):
        return None
    value = raw.get("tmdb")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _offset(record: Mapping[str, Any]) -> int:
    """Where this entry starts inside its TMDB season (TMDB episode = 1 + it)."""
    raw = record.get("episode_offset")
    if not isinstance(raw, dict):
        return 0
    value = raw.get("tmdb")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _ambiguous(reason: str, episode: int, entries: Sequence[tuple[int, int]]) -> PlayTarget:
    """A target that says which entries the episode could belong to."""
    candidates = tuple(
        PlayTarget(
            anilist_id=anilist_id,
            episode=max(1, episode - offset),
            confident=True,
            reason=f"starts at TMDB episode {offset + 1}",
        )
        for offset, anilist_id in entries
    )
    return PlayTarget(0, max(1, episode), False, reason, candidates)


def _from_tv(
    records: Sequence[Mapping[str, Any]], tmdb_id: int, season: int, episode: int
) -> PlayTarget | None:
    """The AniList entry for one TMDB episode of a show, if the list says."""
    in_season: list[tuple[int, int]] = []  # (offset, anilist id)
    seen: set[int] = set()
    for record in records:
        if tmdb_id not in _tmdb_ids(record, "tv"):
            continue
        anilist_id = _anilist_id(record)
        if anilist_id is None or _season(record) != season:
            continue
        if anilist_id in seen:
            continue
        seen.add(anilist_id)
        in_season.append((_offset(record), anilist_id))
    if not in_season:
        return None
    in_season.sort()
    number = max(1, int(episode))
    covered = [(offset, anilist_id) for offset, anilist_id in in_season if offset < number]
    if not covered:
        return _ambiguous(
            "the mapped entries start after this episode", number, in_season
        )
    start = max(offset for offset, _ in covered)
    winners = sorted(
        anilist_id for offset, anilist_id in covered if offset == start
    )
    if len(winners) > 1:
        return _ambiguous(
            f"{len(winners)} AniList entries share this TMDB season and episode",
            number,
            [(start, anilist_id) for anilist_id in winners],
        )
    return PlayTarget(winners[0], number - start, True, "mapped by the anime list")


def _from_movie(
    records: Sequence[Mapping[str, Any]], tmdb_id: int
) -> PlayTarget | None:
    """The AniList entry for a TMDB film, if the list names exactly one."""
    found = sorted(
        {
            anilist_id
            for record in records
            if tmdb_id in _tmdb_ids(record, "movie")
            and (anilist_id := _anilist_id(record)) is not None
        }
    )
    if not found:
        return None
    if len(found) > 1:
        return _ambiguous(
            f"{len(found)} AniList entries share this TMDB film",
            1,
            [(0, anilist_id) for anilist_id in found],
        )
    return PlayTarget(found[0], 1, True, "mapped film")


# -- public lookups ---------------------------------------------------------


def tmdb_to_anilist(
    tmdb_kind: str,
    tmdb_id: int,
    season: int = 1,
    episode: int = 1,
    *,
    title: str = "",
    cache_path: Path | str | None = None,
    force_refresh: bool = False,
    now: float | None = None,
    ttl: float = WEEK,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
    search: Callable[[str], int | None] | None = None,
) -> PlayTarget | None:
    """Where to play TMDB's ``tmdb_id`` (a season and episode) on ani.pm.

    The mapping list answers first.  When it knows nothing about the title, the
    TMDB *title* is searched on AniList through :func:`streaming.anilist_search`
    (the same exact-name search the anime pages already use) and an AniList id
    is used with the episode number unchanged.  With no title, no list entry
    and no search hit the answer is ``None``: nothing is guessed.

    ``season``/``episode`` default to the first of each, which is what a film
    and a one-season show need.  ``fetch``/``search`` are injection points for
    tests; production calls leave them out.
    """
    kind = "tv" if str(tmdb_kind).lower() == "tv" else "movie"
    number = _number(tmdb_id)
    if number is None:
        return None
    records = load_mapping(
        cache_path=cache_path, force=force_refresh, now=now, ttl=ttl, fetch=fetch
    ).records
    if kind == "movie":
        mapped = _from_movie(records, number)
    else:
        try:
            wanted = max(0, int(season))
        except (TypeError, ValueError):
            wanted = 1
        mapped = _from_tv(records, number, wanted, episode)
    if mapped is not None:
        return mapped
    text = " ".join(str(title or "").split())
    if not text:
        return None
    finder = search or streaming.anilist_search
    try:
        found = finder(text)
    except Exception:  # noqa: BLE001 - a search failure is simply no link
        return None
    anilist_id = _number(found)
    if anilist_id is None:
        return None
    played = 1 if kind == "movie" else max(1, int(episode))
    return PlayTarget(anilist_id, played, True, "matched by title on AniList")


def anilist_to_tmdb(
    anilist_id: int,
    *,
    cache_path: Path | str | None = None,
    force_refresh: bool = False,
    now: float | None = None,
    ttl: float = WEEK,
    fetch: Callable[[], list[dict[str, Any]]] | None = None,
) -> tuple[str, int, int] | None:
    """Where an AniList entry sits in TMDB: ``(kind, tmdb_id, season)``.

    A show is preferred over a film when an entry is linked to both (a season
    and its compilation film share the AniList id).  Films come back with
    season 0, shows with the TMDB season the list records, defaulting to 1.
    """
    number = _number(anilist_id)
    if number is None:
        return None
    records = load_mapping(
        cache_path=cache_path, force=force_refresh, now=now, ttl=ttl, fetch=fetch
    ).records
    matches = [record for record in records if _anilist_id(record) == number]
    if not matches:
        return None
    for kind in ("tv", "movie"):
        for record in matches:
            ids = _tmdb_ids(record, kind)
            if not ids:
                continue
            season = _season(record) if kind == "tv" else 0
            return kind, ids[0], season if season is not None else 1
    return None
