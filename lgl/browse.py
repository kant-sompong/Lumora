"""Pure helpers for the Browse page: size buckets and facet counts.

The Browse sidebar shows counts next to each option ("Action 1,284").  Those
numbers are computed here, once per refresh, instead of inside the widget code,
so they can be tested without a display and cannot silently disagree with the
filter that uses them: both call :func:`size_matches` / the same casefold rule.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .genres import genre_options

GIB = 1024 ** 3

#: Size chips, in the order they appear.  The labels are the ones users see.
SIZE_FILTERS = (
    ("all", "Any"),
    ("under10", "<10 GB"),
    ("under50", "<50 GB"),
    ("under100", "<100 GB"),
)

_SIZE_LIMITS: dict[str, int] = {
    "under10": 10 * GIB,
    "under50": 50 * GIB,
    "under100": 100 * GIB,
}


def size_matches(size: int | None, bucket: str) -> bool:
    """Is ``size`` inside the bucket?  Unknown sizes never match a bucket."""
    if bucket in (None, "", "all"):
        return True
    limit = _SIZE_LIMITS.get(str(bucket))
    if limit is None:
        return True
    if not size or size <= 0:
        return False
    return size < limit


def size_counts(games: Iterable[object]) -> dict[str, int]:
    """How many games each size chip would leave, ignoring every other filter."""
    sizes = [getattr(game, "item_size", None) for game in games]
    return {
        bucket: sum(1 for size in sizes if size_matches(size, bucket))
        for bucket, _label in SIZE_FILTERS
    }


def genre_counts(
    games: Iterable[object], titles: Iterable[str] = ()
) -> list[tuple[str, int]]:
    """Genre labels that qualify, each with the number of games carrying it.

    A label qualifies by the same rule as the Genre filter in the Library view
    (:func:`lgl.genres.genre_options`), so provenance tags and titles are not
    offered.  Counting is case-insensitive and whitespace-normalised, which is
    how ``_filter_archive`` compares them too.
    """
    tag_counts: dict[str, int] = {}
    #: The spelling a tag was first seen with, so "Action RPG" is not shown as
    #: the lower-cased lookup key while the filter compares case-insensitively.
    display: dict[str, str] = {}
    for game in games:
        seen: set[str] = set()
        for tag in getattr(game, "subjects", ()) or ():
            text = " ".join(str(tag).split())
            key = text.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            tag_counts[key] = tag_counts.get(key, 0) + 1
            display.setdefault(key, text)
    labels = genre_options(tag_counts, titles=titles)
    return [
        (display.get(label.casefold(), label), tag_counts.get(label.casefold(), 0))
        for label in labels
    ]


def count_label(value: int) -> str:
    """Thousands-separated count, the way the header shows them."""
    return f"{int(value):,}"
