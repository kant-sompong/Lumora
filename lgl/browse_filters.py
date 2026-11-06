"""The advanced Browse filters that both frontends share.

:mod:`lgl.content_filters` owns the matching — runtime, language, rating, year
range and include/exclude genres — and :mod:`lgl.shelves` already uses it.  This
module only bridges a :class:`~lgl.archive.GameSummary` to the mapping that
``content_filters.matches`` reads, and turns the choices the two filter columns
offer into one ``Filters`` value and the summary line they show.

Only year and genres are ever set.  ``GameSummary`` carries ``year``,
``subjects``, ``item_size``, ``creator`` and ``licenseurl`` and has no runtime,
language or rating, so offering those three would be a control that can never
match anything.

Genre names are casefolded on the way in because that is how
``content_filters`` compares them; the filter columns keep the collection's own
spelling for display, which is why :func:`summary` takes the display names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from . import content_filters
from .archive import GameSummary
from .content_filters import EMPTY, MAX_RUNTIME, Filters

YEAR = "year"
GENRES = "genres"

#: The decade shortcuts the Released chips offer, as inclusive year bounds.
YEAR_BUCKETS: dict[str, tuple[int | None, int | None]] = {
    "pre1990": (None, 1989),
    "1990s": (1990, 1999),
    "2000s": (2000, 2009),
    "2010s": (2010, 2019),
    "2020s": (2020, None),
}


def year_number(value: object) -> int | None:
    """The four-digit year inside a value, or ``None`` when there is not one."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = int(text[:4])
    except (TypeError, ValueError):
        return None
    return number if 1900 <= number <= 2200 else None


def bucket_bounds(bucket: str) -> tuple[int | None, int | None]:
    """The inclusive ``(from, to)`` years a Released chip stands for."""
    return YEAR_BUCKETS.get(str(bucket or ""), (None, None))


def bucket_for(min_year: int | None, max_year: int | None) -> str:
    """The Released chip that matches this range exactly, or ``"all"``."""
    for bucket, bounds in YEAR_BUCKETS.items():
        if bounds == (min_year, max_year):
            return bucket
    return "all"


def item(game: GameSummary) -> dict[str, object]:
    """The mapping ``content_filters.matches`` reads, for one game."""
    return {"year": game.year, "genres": list(game.subjects or ())}


def filters_for(
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    genres: Iterable[str] = (),
    exclude_genres: Iterable[str] = (),
) -> Filters:
    """A ``Filters`` value from the year range and genre choices of a column."""
    low, high = min_year, max_year
    if low is not None and high is not None and low > high:
        low, high = high, low
    return Filters(
        min_year=low,
        max_year=high,
        genres=_names(genres),
        exclude_genres=_names(exclude_genres),
    )


def _names(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = {
        " ".join(str(value).split()).casefold()
        for value in values or ()
        if str(value or "").strip()
    }
    return tuple(sorted(cleaned))


def matches(game: GameSummary, filters: Filters = EMPTY) -> bool:
    """Does one game pass these filters?"""
    return content_filters.matches(item(game), filters)


def apply(
    games: Sequence[GameSummary], filters: Filters = EMPTY
) -> list[GameSummary]:
    """The games that pass, in the order they came in."""
    if filters.is_empty():
        return list(games)
    return [game for game in games if matches(game, filters)]


def summary(
    filters: Filters = EMPTY, display: Mapping[str, str] | None = None
) -> str:
    """The line shown under the filter column, or "" when nothing is filtered.

    Built on :func:`lgl.content_filters.describe`, with two differences: the
    result reads as a sentence ("Showing 1992-2005, Puzzle, not Action"), and
    genre names are put back into the spelling the collection uses, because the
    ``Filters`` value stores them casefolded for matching.
    """
    if filters.is_empty():
        return ""
    names = {
        str(key).strip().casefold(): str(value)
        for key, value in (display or {}).items()
    }
    parts: list[str] = []
    for part in content_filters.describe(filters).split(", "):
        if part.startswith("genre "):
            parts.append(_shown(part[len("genre "):], names))
        elif part.startswith("without "):
            parts.append("not " + _shown(part[len("without "):], names))
        else:
            parts.append(part)
    return "Showing " + ", ".join(parts)


def _shown(names: str, display: Mapping[str, str]) -> str:
    return "/".join(display.get(name, name) for name in names.split("/"))


def available(games: Iterable[GameSummary]) -> set[str]:
    """Which filters these games can match: a subset of ``{YEAR, GENRES}``.

    The filter columns show a section only when it is in this set, so a control
    is never offered for a field no loaded title carries.
    """
    found: set[str] = set()
    for game in games:
        if YEAR not in found and year_number(game.year) is not None:
            found.add(YEAR)
        if GENRES not in found and any(
            str(tag).strip() for tag in (game.subjects or ())
        ):
            found.add(GENRES)
        if len(found) == 2:
            break
    return found


__all__ = [
    "EMPTY",
    "GENRES",
    "MAX_RUNTIME",
    "YEAR",
    "YEAR_BUCKETS",
    "Filters",
    "apply",
    "available",
    "bucket_bounds",
    "bucket_for",
    "filters_for",
    "item",
    "matches",
    "summary",
    "year_number",
]
