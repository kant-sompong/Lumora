"""Comparing anime across seasons: what carried over, what is new, what ended."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

SEASON_ORDER = ("winter", "spring", "summer", "fall")


def season_key(item: Mapping[str, Any]) -> str:
    """``2024 spring`` style key from whatever the source provided."""
    for raw in (item.get("season_key"), item.get("season_title")):
        text = str(raw or "").strip()
        if text:
            return text.casefold()
    year = str(item.get("year") or "").strip()
    season = str(item.get("season") or "").strip().casefold()
    if season in SEASON_ORDER and year:
        return f"{year} {season}"
    return season or year


def _key(item: Mapping[str, Any]) -> str:
    return str(item.get("key") or item.get("identifier") or item.get("title") or "").strip().casefold()


def group(items: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        key = season_key(item)
        if key:
            buckets[key].append(item)
    for entries in buckets.values():
        entries.sort(key=lambda item: _key(item))
    return dict(sorted(buckets.items()))


def compare(
    items: Iterable[Mapping[str, Any]],
    first: str,
    second: str,
) -> dict[str, Any]:
    """Titles shared between two seasons and what each season has alone."""
    buckets = group(items)
    left = {_key(item): item for item in buckets.get(str(first).strip().casefold(), [])}
    right = {_key(item): item for item in buckets.get(str(second).strip().casefold(), [])}
    shared = sorted(set(left) & set(right))
    only_first = sorted(set(left) - set(right))
    only_second = sorted(set(right) - set(left))
    return {
        "first": str(first).strip().casefold(),
        "second": str(second).strip().casefold(),
        "shared": [left[key] for key in shared],
        "only_first": [left[key] for key in only_first],
        "only_second": [right[key] for key in only_second],
        "counts": {
            "first": len(left),
            "second": len(right),
            "shared": len(shared),
        },
    }


def season_title(key: Any) -> str:
    """``2024 winter`` as ``Winter 2024``, the wording of the pickers.

    Anything that is not a ``year season`` key is returned as it came in.
    """
    parts = str(key or "").split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].casefold() in SEASON_ORDER:
        return f"{parts[1].capitalize()} {parts[0]}"
    return str(key or "").strip()


def summary(result: Mapping[str, Any]) -> str:
    counts = result.get("counts") or {}
    first = season_title(result.get("first", ""))
    second = season_title(result.get("second", ""))
    if not counts:
        return "Nothing to compare"
    return (
        f"{first}: {counts.get('first', 0)} titles, {second}: {counts.get('second', 0)} titles, "
        f"{counts.get('shared', 0)} in both"
    )


def seasons(items: Iterable[Mapping[str, Any]]) -> list[str]:
    return list(group(items))


__all__ = [
    "SEASON_ORDER",
    "compare",
    "group",
    "season_key",
    "season_title",
    "seasons",
    "summary",
]
