"""Advanced content filters: runtime, language, rating, year and genre.

A filter is a frozen dataclass so it can be cached, compared and stored in a
collection without the caller worrying about mutation. `parse` never raises: a
stored filter with junk in it degrades to the subset it can understand.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_RUNTIME = 24 * 60


@dataclass(frozen=True)
class Filters:
    min_runtime: int | None = None
    max_runtime: int | None = None
    languages: tuple[str, ...] = field(default_factory=tuple)
    min_rating: float | None = None
    min_year: int | None = None
    max_year: int | None = None
    genres: tuple[str, ...] = field(default_factory=tuple)
    exclude_genres: tuple[str, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return self == EMPTY


EMPTY = Filters()


def _int(value: Any, low: int = 0, high: int = MAX_RUNTIME) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(10.0, number))


def _text_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = [value]
    elif isinstance(value, Iterable):
        parts = [str(item) for item in value]
    else:
        return ()
    cleaned = {part.strip().casefold() for part in parts if str(part).strip()}
    return tuple(sorted(cleaned))


def parse(raw: Mapping[str, Any] | None) -> Filters:
    data = dict(raw or {}) if isinstance(raw, Mapping) else {}
    minimum = _int(data.get("min_runtime"))
    maximum = _int(data.get("max_runtime"))
    if minimum is not None and maximum is not None and minimum > maximum:
        minimum, maximum = maximum, minimum
    first_year = _int(data.get("min_year"), 1900, 2200)
    last_year = _int(data.get("max_year"), 1900, 2200)
    if first_year is not None and last_year is not None and first_year > last_year:
        first_year, last_year = last_year, first_year
    return Filters(
        min_runtime=minimum,
        max_runtime=maximum,
        languages=_text_tuple(data.get("languages")),
        min_rating=_float(data.get("min_rating")),
        min_year=first_year,
        max_year=last_year,
        genres=_text_tuple(data.get("genres")),
        exclude_genres=_text_tuple(data.get("exclude_genres")),
    )


def _item_genres(item: Mapping[str, Any]) -> set[str]:
    raw = item.get("genres") or ()
    if isinstance(raw, str):
        raw = [part for part in raw.split(",") if part.strip()]
    return {str(part).strip().casefold() for part in raw if str(part).strip()}


def matches(item: Mapping[str, Any], filters: Filters = EMPTY) -> bool:
    if filters.is_empty():
        return True
    runtime = _int(item.get("runtime"), 0, 10 * MAX_RUNTIME)
    if filters.min_runtime is not None and (runtime is None or runtime < filters.min_runtime):
        return False
    if filters.max_runtime is not None and (runtime is None or runtime > filters.max_runtime):
        return False
    if filters.languages:
        language = str(item.get("language") or "").strip().casefold()
        if language not in filters.languages:
            return False
    if filters.min_rating is not None:
        rating = _float(item.get("rating"))
        if rating is None or rating < filters.min_rating:
            return False
    year = _int(item.get("year"), 1900, 2200)
    if filters.min_year is not None and (year is None or year < filters.min_year):
        return False
    if filters.max_year is not None and (year is None or year > filters.max_year):
        return False
    genres = _item_genres(item)
    if filters.genres and not (genres & set(filters.genres)):
        return False
    return not (filters.exclude_genres and (genres & set(filters.exclude_genres)))


def apply(items: Iterable[Mapping[str, Any]], filters: Filters = EMPTY) -> list[Mapping[str, Any]]:
    return [item for item in items if matches(item, filters)]


def describe(filters: Filters = EMPTY) -> str:
    if filters.is_empty():
        return "No content filters"
    parts: list[str] = []
    if filters.min_runtime is not None or filters.max_runtime is not None:
        low = filters.min_runtime if filters.min_runtime is not None else 0
        high = filters.max_runtime if filters.max_runtime is not None else MAX_RUNTIME
        parts.append(f"{low}-{high} min")
    if filters.languages:
        parts.append("language " + "/".join(filters.languages))
    if filters.min_rating is not None:
        parts.append(f"rated {filters.min_rating:g}+")
    if filters.min_year is not None or filters.max_year is not None:
        parts.append(f"{filters.min_year or 1900}-{filters.max_year or 'now'}")
    if filters.genres:
        parts.append("genre " + "/".join(filters.genres))
    if filters.exclude_genres:
        parts.append("without " + "/".join(filters.exclude_genres))
    return ", ".join(parts)


__all__ = ["EMPTY", "MAX_RUNTIME", "Filters", "apply", "describe", "matches", "parse"]
