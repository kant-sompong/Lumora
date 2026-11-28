"""Searchable episode calendar: which episodes of a series air next.

The release calendar (:mod:`lgl.release_feed`) answers "what is out this week".
This answers "when does the next episode of *this* show air", for anime and TV
alike, and it is careful about what it does not know.

* **Anime** — AniList's airing schedule: exact air times, no key.
* **TV** — TMDB: search, then the show's recent seasons for episode air dates.

TMDB reports a calendar day, not a time, and future episodes often have no date
yet.  Those stay "Air date unknown" unless the show has a demonstrably regular
weekly cadence: at least two confirmed dates whose median gap is 5-10 days.
Even then only the next few episodes are projected, and each one is labelled
estimated.  No cadence, no guess.

Everything the network returns is untrusted: bad rows are skipped and a failed
source becomes a message.  Network access is passed in, which is how the tests
run without it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Any, Callable, Mapping

from . import tmdb
from .anime_calendar import parse_when, zone
from .release_feed import anilist_post

#: How far ahead the calendar looks when someone searches.
DEFAULT_HORIZON_DAYS = 90
#: Series shown per source, so one search cannot pull in a whole catalogue.
MAX_SERIES = 3
#: Future episodes a regular cadence may project.
MAX_ESTIMATES = 3
#: Undated episodes listed per season before giving up.
MAX_UNKNOWN = 2
#: A cadence outside these days is not regular enough to project from.
MIN_CADENCE_DAYS = 5
MAX_CADENCE_DAYS = 10

CONFIRMED = "Confirmed"
ESTIMATED = "Estimated"
UNKNOWN_DATE = "Air date unknown"

_ANILIST_SEARCH = """
query ($search: String) {
  Page(page: 1, perPage: 8) {
    media(search: $search, type: ANIME, isAdult: false, sort: SEARCH_MATCH) {
      id
      title { romaji english }
      airingSchedule(notYetAired: true, perPage: 20) {
        nodes { episode airingAt }
      }
    }
  }
}
"""


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _day_epoch(value: Any) -> float | None:
    """``YYYY-MM-DD`` to midnight UTC, or ``None`` for anything else."""
    text = str(value or "").strip()[:10]
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).timestamp()


def episode_label(season: int | None, episode: int | None) -> str:
    """``S2E5`` when both are known, ``E5`` or ``S2`` when only one is."""
    if season and episode:
        return f"S{season}E{episode}"
    if episode:
        return f"E{episode}"
    if season:
        return f"S{season}"
    return ""


@dataclass(frozen=True)
class Airing:
    """One episode of a series, with how sure we are of its date."""

    series: str
    episode: int | None = None
    air_at: float | None = None
    confirmed: bool = False
    source: str = ""
    season: int | None = None
    #: The provider gave a day but no time (TMDB): show the date only.
    date_only: bool = False
    #: Provider key of the series, e.g. ``anilist:1``.
    key: str = ""

    @property
    def known(self) -> bool:
        return self.air_at is not None

    @property
    def estimated(self) -> bool:
        """A projected date: we know roughly when, not exactly."""
        return self.air_at is not None and not self.confirmed

    @property
    def label(self) -> str:
        return episode_label(self.season, self.episode)

    @property
    def dedupe_key(self) -> tuple[str, int | None, int | None]:
        return (tmdb.normalise_title(self.series), self.season, self.episode)


@dataclass(frozen=True)
class SeriesGroup:
    """A series and its upcoming episodes, soonest first."""

    title: str
    source: str
    airings: tuple[Airing, ...]

    @property
    def key(self) -> str:
        return tmdb.normalise_title(self.title) or self.title.casefold()


@dataclass
class CalendarResult:
    query: str = ""
    groups: list[SeriesGroup] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def airings(self) -> list[Airing]:
        return [airing for group in self.groups for airing in group.airings]


# -- estimation ------------------------------------------------------------

def estimate(
    anchors: list[tuple[int, float]],
    numbers: list[int],
    *,
    now: float,
    horizon: float,
    limit: int = MAX_ESTIMATES,
) -> list[tuple[int, float]]:
    """Project dates for undated episodes, or ``[]`` when there is no pattern.

    ``anchors`` are ``(episode number, air epoch)`` for confirmed episodes and
    ``numbers`` the episodes still missing a date.  A projection needs at least
    two confirmed dates, a median gap inside 5-10 days, and no wild gap; every
    projected date must still be ahead of ``now`` and inside ``horizon``.
    """
    if len(anchors) < 2 or not numbers:
        return []
    gaps = [(later[1] - earlier[1]) / 86400 for earlier, later in zip(anchors, anchors[1:])]
    if not gaps:
        return []
    step = median(gaps)
    if not MIN_CADENCE_DAYS <= step <= MAX_CADENCE_DAYS:
        return []
    if any(gap < MIN_CADENCE_DAYS - 1 or gap > MAX_CADENCE_DAYS + 2 for gap in gaps):
        return []
    _, last_at = anchors[-1]
    projected: list[tuple[int, float]] = []
    for offset, number in enumerate(numbers, start=1):
        when = last_at + offset * step * 86400
        if when < now or when > horizon:
            break
        projected.append((number, when))
        if len(projected) >= limit:
            break
    return projected


# -- sources ---------------------------------------------------------------

def fetch_anilist(
    query: str,
    *,
    now: float,
    horizon: float,
    post: Callable[[Mapping[str, Any]], Any] = anilist_post,
    limit: int = MAX_SERIES,
) -> list[tuple[str, list[Airing]]]:
    """Anime matching ``query`` (title, episodes) from AniList's schedule."""
    payload = post({"query": _ANILIST_SEARCH, "variables": {"search": query}})
    block = ((payload or {}).get("data") or {}).get("Page") if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        raise ValueError("AniList returned an unexpected document.")
    found: list[tuple[str, list[Airing]]] = []
    for media in block.get("media") or []:
        if not isinstance(media, dict):
            continue
        ident = _int(media.get("id"))
        titles = media.get("title") if isinstance(media.get("title"), dict) else {}
        title = str(titles.get("english") or titles.get("romaji") or "").strip()
        if ident is None or not title:
            continue
        schedule = media.get("airingSchedule") if isinstance(media.get("airingSchedule"), dict) else {}
        airings: list[Airing] = []
        for node in schedule.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            when = parse_when(node.get("airingAt"))
            if when is None or when < now - 86400 or when > horizon:
                continue
            airings.append(Airing(
                series=title,
                episode=_int(node.get("episode")),
                air_at=when,
                confirmed=True,
                source="AniList",
                key=f"anilist:{ident}",
            ))
        if airings:
            airings.sort(key=lambda airing: (airing.air_at or 0.0, airing.episode or 0))
            found.append((title, airings))
        if len(found) >= limit:
            break
    return found


def _season_airings(
    data: Mapping[str, Any],
    title: str,
    ident: int,
    season: int,
    *,
    now: float,
    horizon: float,
) -> list[Airing]:
    """Confirmed episodes in the window, plus in-season projections and unknowns."""
    anchors: list[tuple[int, float]] = []
    undated: list[int] = []
    out: list[Airing] = []
    for row in data.get("episodes") or []:
        if not isinstance(row, dict):
            continue
        number = _int(row.get("episode_number"))
        if number is None:
            continue
        when = _day_epoch(row.get("air_date"))
        if when is not None and now - 86400 <= when <= horizon:
            anchors.append((number, when))
            out.append(Airing(title, number, when, True, "TMDB", season, True, f"tmdb:tv:{ident}"))
        elif when is None:
            undated.append(number)
    anchors.sort()
    after_last = sorted(number for number in undated if number > (anchors[-1][0] if anchors else 0))
    projected = estimate(anchors, after_last, now=now, horizon=horizon)
    taken = {number for number, _ in projected}
    for number, when in projected:
        out.append(Airing(title, number, when, False, "TMDB", season, True, f"tmdb:tv:{ident}"))
    for number in [n for n in after_last if n not in taken][:MAX_UNKNOWN]:
        out.append(Airing(title, number, None, False, "TMDB", season, True, f"tmdb:tv:{ident}"))
    out.sort(key=lambda airing: (airing.air_at is None, airing.air_at or 0.0, airing.episode or 0))
    return out


def _tmdb_airings(
    ident: int,
    title: str,
    *,
    now: float,
    horizon: float,
    get: Callable[[str, Mapping[str, Any]], Any],
) -> list[Airing]:
    """Upcoming episodes of one show, from its two most recent seasons."""
    detail = get(f"/tv/{ident}", {})
    if not isinstance(detail, dict):
        return []
    seasons = [
        row for row in detail.get("seasons") or []
        if isinstance(row, dict) and (_int(row.get("season_number")) or 0) > 0
    ]
    seasons.sort(key=lambda row: _int(row.get("season_number")) or 0, reverse=True)
    airings: list[Airing] = []
    for row in seasons[:2]:
        number = _int(row.get("season_number"))
        if number is None:
            continue
        try:
            data = get(f"/tv/{ident}/season/{number}", {})
        except Exception:  # noqa: BLE001 - one missing season must not sink the show
            continue
        if isinstance(data, dict):
            airings.extend(_season_airings(data, title, ident, number, now=now, horizon=horizon))
    return airings


def fetch_tv(
    query: str,
    *,
    now: float,
    horizon: float,
    get: Callable[[str, Mapping[str, Any]], Any],
    limit: int = MAX_SERIES,
) -> list[tuple[str, list[Airing]]]:
    """TV shows matching ``query`` (title, episodes) from TMDB."""
    listing = get("/search/tv", {"query": query, "include_adult": "false"})
    shows = [
        row for row in (listing or {}).get("results") or []
        if isinstance(row, dict) and _int(row.get("id")) is not None
    ]
    found: list[tuple[str, list[Airing]]] = []
    for show in shows[:limit]:
        ident = _int(show.get("id"))
        title = str(show.get("name") or "").strip()
        if ident is None or not title:
            continue
        airings = _tmdb_airings(ident, title, now=now, horizon=horizon, get=get)
        if airings:
            found.append((title, airings))
    return found


# -- merging ---------------------------------------------------------------

def merge(series: list[tuple[str, str, list[Airing]]]) -> list[tuple[str, str, list[Airing]]]:
    """Drop repeated episodes, and a TMDB show AniList already covers.

    Each tuple is ``(title, source, airings)``.  AniList's exact air times win
    over TMDB's bare dates for the same series, and an episode appears once per
    ``(series, season, number)`` even when two sources list it.
    """
    anime_titles = {tmdb.normalise_title(title) for title, source, _ in series if source == "AniList"}
    seen: set[tuple[str, int | None, int | None]] = set()
    merged: list[tuple[str, str, list[Airing]]] = []
    for title, source, airings in series:
        normal = tmdb.normalise_title(title)
        if source == "TMDB" and normal in anime_titles:
            continue
        kept: list[Airing] = []
        for airing in airings:
            key = (normal, airing.season, airing.episode)
            if key in seen:
                continue
            seen.add(key)
            kept.append(airing)
        if kept:
            merged.append((title, source, kept))
    return merged


def group(series: list[tuple[str, str, list[Airing]]]) -> list[SeriesGroup]:
    """Series groups, soonest episode first, undated ones last."""
    groups: list[SeriesGroup] = []
    for title, source, airings in series:
        ordered = sorted(
            airings, key=lambda airing: (airing.air_at is None, airing.air_at or 0.0, airing.episode or 0)
        )
        groups.append(SeriesGroup(title, source, tuple(ordered)))
    groups.sort(key=lambda item: (
        min((airing.air_at for airing in item.airings if airing.air_at is not None), default=float("inf")),
        item.title.casefold(),
    ))
    return groups


def search(
    query: str,
    *,
    now: float | None = None,
    days: int = DEFAULT_HORIZON_DAYS,
    anilist: Callable[[Mapping[str, Any]], Any] = anilist_post,
    tmdb_get: Callable[[str, Mapping[str, Any]], Any] | None = None,
    max_series: int = MAX_SERIES,
) -> CalendarResult:
    """Upcoming episodes of the series matching ``query``."""
    text = str(query or "").strip()
    current = time.time() if now is None else float(now)
    try:
        span = max(1, int(days))
    except (TypeError, ValueError):
        span = DEFAULT_HORIZON_DAYS
    horizon = current + span * 86400
    result = CalendarResult(query=text)
    if not text:
        return result

    series: list[tuple[str, str, list[Airing]]] = []
    try:
        for title, airings in fetch_anilist(text, now=current, horizon=horizon, post=anilist, limit=max_series):
            series.append((title, "AniList", airings))
    except Exception as exc:  # noqa: BLE001 - one source failing is a message
        result.errors.append(f"Could not search AniList: {exc}")
    if tmdb_get is None:
        result.errors.append("TV search needs a TMDB key (Settings → Media).")
    else:
        try:
            for title, airings in fetch_tv(text, now=current, horizon=horizon, get=tmdb_get, limit=max_series):
                series.append((title, "TMDB", airings))
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"Could not search TMDB: {exc}")

    result.groups = group(merge(series))
    return result


# -- wording ---------------------------------------------------------------

def format_date(airing: Airing, tz: str = "local") -> str:
    """The date a row shows, with a time only when the provider gave one."""
    if not airing.known:
        return UNKNOWN_DATE
    stamp = airing.air_at or 0.0
    if airing.date_only:
        # A bare date is a calendar day, not an instant: never shift it by zone.
        return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%a %d %b %Y")
    moment = datetime.fromtimestamp(stamp, tz=zone(tz))
    return moment.strftime("%a %d %b %Y %H:%M") + f" {moment.tzname() or tz}"


def status_label(airing: Airing) -> str:
    """``Confirmed``, ``Estimated``, or ``""`` when the date is unknown."""
    if not airing.known:
        return ""
    return CONFIRMED if airing.confirmed else ESTIMATED


def row_text(airing: Airing, tz: str = "local") -> str:
    """``S2E5 · Sat 05 Dec 2026 · Estimated`` — one row's single line."""
    bits = [bit for bit in (airing.label, format_date(airing, tz), status_label(airing)) if bit]
    return " · ".join(bits)


__all__ = [
    "Airing",
    "CONFIRMED",
    "CalendarResult",
    "DEFAULT_HORIZON_DAYS",
    "ESTIMATED",
    "MAX_CADENCE_DAYS",
    "MAX_ESTIMATES",
    "MAX_SERIES",
    "MAX_UNKNOWN",
    "MIN_CADENCE_DAYS",
    "SeriesGroup",
    "UNKNOWN_DATE",
    "episode_label",
    "estimate",
    "fetch_anilist",
    "fetch_tv",
    "format_date",
    "group",
    "merge",
    "row_text",
    "search",
    "status_label",
]
