"""Finding something to watch: random picks, recommendations, similarity,
hidden gems and suggestions for an incomplete collection.

Everything here is a pure function over plain mappings (``key``, ``title``,
``genres``, ``tags``, ``rating``, ``popularity``), so the same code serves the
archive catalogue, the library and the media lists without knowing about any of
them.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _key(item: Mapping[str, Any]) -> str:
    return str(item.get("key") or item.get("identifier") or item.get("title") or "").strip().casefold()


def _labels(item: Mapping[str, Any]) -> set[str]:
    values: list[str] = []
    for field in ("genres", "tags"):
        raw = item.get(field) or ()
        if isinstance(raw, str):
            raw = [part for part in raw.replace(";", ",").split(",")]
        values.extend(str(part).strip().casefold() for part in raw if str(part).strip())
    return set(values)


def _rating(item: Mapping[str, Any]) -> float:
    try:
        return max(0.0, min(10.0, float(item.get("rating") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _popularity(item: Mapping[str, Any]) -> float:
    try:
        return max(0.0, float(item.get("popularity") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def similarity(first: Mapping[str, Any], second: Mapping[str, Any]) -> float:
    """Jaccard overlap of genres and tags, 0 when they share nothing."""
    left, right = _labels(first), _labels(second)
    if not left or not right:
        return 0.0
    shared = len(left & right)
    return shared / len(left | right) if shared else 0.0


def similar(items: Sequence[Mapping[str, Any]], seed: str, limit: int = 6) -> list[Mapping[str, Any]]:
    """Titles most like the one identified by `seed`, best first."""
    wanted = str(seed or "").strip().casefold()
    source = next((item for item in items if _key(item) == wanted), None)
    if source is None:
        return []
    scored = [
        (similarity(source, item), item)
        for item in items
        if _key(item) != wanted
    ]
    scored = [(value, item) for value, item in scored if value > 0]
    scored.sort(key=lambda pair: (-pair[0], _key(pair[1])))
    return [item for _value, item in scored[: max(1, int(limit))]]


def random_pick(items: Sequence[Mapping[str, Any]], *, rng: random.Random | None = None) -> Mapping[str, Any] | None:
    """One title, or None when there is nothing to pick from."""
    pool = list(items)
    if not pool:
        return None
    generator = rng or random.Random()
    return generator.choice(pool)


def hidden_gems(
    items: Iterable[Mapping[str, Any]],
    limit: int = 10,
    *,
    max_popularity: float = 40.0,
    min_rating: float = 7.0,
) -> list[Mapping[str, Any]]:
    """Well rated but not widely known, best rated first."""
    gems = [
        item
        for item in items
        if _popularity(item) <= max_popularity and _rating(item) >= min_rating
    ]
    gems.sort(key=lambda item: (-_rating(item), _popularity(item), _key(item)))
    return gems[: max(1, int(limit))]


def recommend(
    items: Sequence[Mapping[str, Any]],
    liked: Iterable[str],
    limit: int = 8,
) -> list[Mapping[str, Any]]:
    """Titles closest to anything already liked, excluding what was liked."""
    wanted = {str(key).strip().casefold() for key in liked if str(key).strip()}
    seeds = [item for item in items if _key(item) in wanted]
    if not seeds:
        return []
    scored: dict[str, tuple[float, Mapping[str, Any]]] = {}
    for item in items:
        key = _key(item)
        if not key or key in wanted:
            continue
        total = sum(similarity(seed, item) for seed in seeds)
        if total <= 0:
            continue
        current = scored.get(key)
        if current is None or total > current[0]:
            scored[key] = (total, item)
    ranked = sorted(scored.values(), key=lambda pair: (-pair[0], _key(pair[1])))
    return [item for _score, item in ranked[: max(1, int(limit))]]


def suggestions(
    items: Sequence[Mapping[str, Any]],
    owned: Iterable[str],
    limit: int = 8,
) -> list[Mapping[str, Any]]:
    """Titles that would complete the genres of what is already owned."""
    have = {str(key).strip().casefold() for key in owned if str(key).strip()}
    covered: set[str] = set()
    for item in items:
        if _key(item) in have:
            covered |= _labels(item)
    if not covered:
        return []
    scored: list[tuple[int, float, Mapping[str, Any]]] = []
    for item in items:
        key = _key(item)
        if not key or key in have:
            continue
        overlap = len(_labels(item) & covered)
        if overlap:
            scored.append((overlap, _rating(item), item))
    scored.sort(key=lambda row: (-row[0], -row[1], _key(row[2])))
    return [item for _overlap, _rating_value, item in scored[: max(1, int(limit))]]


__all__ = [
    "hidden_gems",
    "random_pick",
    "recommend",
    "similar",
    "similarity",
    "suggestions",
]
