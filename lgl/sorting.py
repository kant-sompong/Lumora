"""Ordering for the grid, the list and the table.

Sorting 8,000 rows must not re-parse 8,000 titles on every click, and a missing
release year must not jump to the top when the direction flips.  Both are
handled here, once, so every view orders the same way:

* natural order, ``Chapter 2`` before ``Chapter 10``;
* case-, accent- and article-insensitive names (``The Long Dark`` under **L**);
* sizes parsed from KB/MB/GB/TB *and* KiB/MiB/GiB/TiB into bytes;
* missing values last **in both directions**, which is the rule people notice
  when it is wrong;
* stable ties by name, then identifier, never flipped by the primary direction;
* ``favorites first`` as a partition on top of the normal order.

Everything here is pure and takes mappings or plain objects, so the GTK views,
the Tkinter frontend and the tests share one implementation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from .titles import sort_name

#: Marker for "this item has no value for that key".
MISSING = object()


@dataclass(frozen=True)
class SortSpec:
    """One entry in the sort menu."""

    key: str
    label: str
    descending: bool
    kind: str  # "text" | "number" | "time" | "flag"


#: The six existing modes plus relevance while searching.  ``descending`` is the
#: default direction, kept per mode so "Release year" still opens newest-first.
SORTS: dict[str, SortSpec] = {
    "recently_played": SortSpec("recently_played", "Recently played", True, "time"),
    "recently_added": SortSpec("recently_added", "Recently added", True, "time"),
    "name": SortSpec("name", "Name", False, "text"),
    "release_year": SortSpec("release_year", "Release year", True, "number"),
    "size": SortSpec("size", "Size", True, "number"),
    "installed_first": SortSpec("installed_first", "Installed first", True, "flag"),
    "relevance": SortSpec("relevance", "Relevance", True, "number"),
}

DEFAULT_SORT = "recently_added"

#: Where each sort key reads its value from, first name that has one wins.
FIELDS: dict[str, tuple[str, ...]] = {
    "recently_played": ("played_at", "last_played", "played"),
    "recently_added": ("added_at", "added", "created_at"),
    "name": ("title", "name"),
    "release_year": ("year", "release_year", "release_date"),
    "size": ("size_bytes", "installed_size", "size"),
    "installed_first": ("installed", "is_installed"),
    "relevance": ("score", "relevance"),
}

_UNITS = {
    "": 1,
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": 1000,
    "kb": 1000,
    "kib": 1024,
    "m": 1000**2,
    "mb": 1000**2,
    "mib": 1024**2,
    "g": 1000**3,
    "gb": 1000**3,
    "gib": 1024**3,
    "t": 1000**4,
    "tb": 1000**4,
    "tib": 1024**4,
    "p": 1000**5,
    "pb": 1000**5,
    "pib": 1024**5,
}
_SIZE = re.compile(r"^([0-9]+(?:[.,][0-9]+)?)\s*([a-zA-Z]*)$")
_LEADING_NUMBER = re.compile(r"^[0-9]+")


def normalize_sort(key: Any) -> str:
    """A stored sort key, or the default when it is unknown."""
    text = str(key or "").strip()
    return text if text in SORTS else DEFAULT_SORT


def spec(key: Any) -> SortSpec:
    return SORTS[normalize_sort(key)]


def fold(text: Any) -> str:
    """Case- and accent-insensitive form: ``"Café"`` -> ``"cafe"``."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def natural_key(text: Any) -> tuple[Any, ...]:
    """Order ``Chapter 2`` before ``Chapter 10``, digits compared as numbers."""
    parts = re.split(r"(\d+)", fold(text))
    return tuple(int(part) if part.isdigit() else part for part in parts)


def parse_size(value: Any) -> int | None:
    """``"2.8 GB"`` / ``"700 MB"`` / ``"1.5 GiB"`` -> bytes, else ``None``.

    Decimal (KB) and binary (KiB) units are both accepted because catalogs mix
    them; guessing one meaning for both would silently reorder a listing.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = " ".join(str(value or "").split())
    if not text:
        return None
    match = _SIZE.match(text)
    if not match:
        return None
    unit = match.group(2).lower()
    if unit not in _UNITS:
        return None
    number = float(match.group(1).replace(",", "."))
    total = int(round(number * _UNITS[unit]))
    return total if total > 0 else None


def parse_time(value: Any) -> float | None:
    """Seconds since the epoch from a number or an ISO-8601 string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return None


def read(item: Any, names: Sequence[str]) -> Any:
    """First present, non-empty field of *item*, whether a mapping or an object."""
    for name in names:
        if isinstance(item, Mapping):
            if name not in item:
                continue
            value = item[name]
        else:
            if not hasattr(item, name):
                continue
            value = getattr(item, name)
        if value is None or value == "" or callable(value):
            continue
        return value
    return None


def value_for(item: Any, key: str) -> tuple[bool, Any]:
    """``(present, comparable value)`` for one sort key.

    ``present`` is what keeps missing metadata out of the top of the list: an
    item without a value is parked at the end whatever the direction.
    """
    chosen = normalize_sort(key)
    raw = read(item, FIELDS.get(chosen, ()))
    if raw is None:
        return False, MISSING
    if chosen == "name":
        return True, natural_key(sort_name(raw))
    if chosen in ("recently_played", "recently_added"):
        value = parse_time(raw)
    elif chosen == "size":
        value = parse_size(raw)
    elif chosen == "release_year":
        match = _LEADING_NUMBER.match(str(raw).strip())
        value = int(match.group(0)) if match else None
    elif chosen == "installed_first":
        value = 1 if raw else 0
        if isinstance(raw, str):
            value = 0 if raw.strip().casefold() in ("", "0", "false", "no", "saved") else 1
    else:  # relevance and any future numeric key
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
    if value is None:
        return False, MISSING
    return True, value


def _identity(item: Any) -> tuple[Any, str]:
    title = read(item, ("title", "name")) or ""
    identifier = read(item, ("identifier", "id", "key", "game_id")) or ""
    return natural_key(sort_name(title)), str(identifier)


def sort_entries(
    entries: Iterable[Any],
    key: str = DEFAULT_SORT,
    *,
    descending: bool | None = None,
    favorites_first: bool = False,
    secondary: str = "",
) -> list[Any]:
    """Sort *entries*, missing values last in both directions.

    The tie-break is always name then identifier, ascending, so reversing the
    primary direction never reverses the names inside one value - that is the
    "do not reverse tie-breakers accidentally" rule.
    """
    chosen = spec(key)
    reverse = chosen.descending if descending is None else bool(descending)
    items = list(entries)

    decorated = []
    for item in items:
        present, value = value_for(item, chosen.key)
        name_key, identifier = _identity(item)
        decorated.append((present, value, name_key, identifier, item))

    present = [row for row in decorated if row[0]]
    absent = [row for row in decorated if not row[0]]

    # Ascending name/id first, then stable passes for secondary and primary.
    present.sort(key=lambda row: (row[2], row[3]))
    absent.sort(key=lambda row: (row[2], row[3]))
    if secondary and secondary in SORTS:
        present.sort(key=lambda row: value_for(row[4], secondary)[1])
    present.sort(key=lambda row: row[1], reverse=reverse)

    ordered = [row[4] for row in present] + [row[4] for row in absent]
    if favorites_first:
        favorites = [
            item for item in ordered
            if bool(read(item, ("favorite", "is_favorite", "favourite")))
        ]
        rest = [
            item for item in ordered
            if not bool(read(item, ("favorite", "is_favorite", "favourite")))
        ]
        ordered = favorites + rest
    return ordered


class SortKeys:
    """Per-item sort keys, computed once and reused across a re-sort.

    Sorting 8,000 titles means parsing 8,000 strings *and* folding accents out
    of them; doing that inside a click handler is what made the old grid feel
    sticky.  The cache is keyed on ``(identifier, key)`` so a rebuilt list of
    plain objects still hits it.
    """

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], tuple[bool, Any]] = {}
        self._identity: dict[str, tuple[Any, str]] = {}
        self.hits = 0
        self.misses = 0

    def _key_of(self, item: Any) -> str:
        identifier = read(item, ("identifier", "id", "key", "game_id")) or ""
        if not identifier:
            identifier = f"@{id(item)}"
        return str(identifier)

    def seed(self, entries: Iterable[Any], keys: Sequence[str]) -> None:
        for item in entries:
            item_key = self._key_of(item)
            self._identity[item_key] = _identity(item)
            for key in keys:
                self._values.setdefault((item_key, normalize_sort(key)), value_for(item, key))

    def value(self, item: Any, key: str) -> tuple[bool, Any]:
        item_key = self._key_of(item)
        cache_key = (item_key, normalize_sort(key))
        cached = self._values.get(cache_key)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        computed = value_for(item, key)
        self._values[cache_key] = computed
        return computed

    def identity(self, item: Any) -> tuple[Any, str]:
        item_key = self._key_of(item)
        cached = self._identity.get(item_key)
        if cached is None:
            cached = _identity(item)
            self._identity[item_key] = cached
        return cached

    def sort(
        self,
        entries: Iterable[Any],
        key: str = DEFAULT_SORT,
        *,
        descending: bool | None = None,
        favorites_first: bool = False,
    ) -> list[Any]:
        chosen = spec(key)
        reverse = chosen.descending if descending is None else bool(descending)
        present: list[tuple[Any, tuple[Any, str], Any]] = []
        absent: list[tuple[tuple[Any, str], Any]] = []
        for item in entries:
            has_value, value = self.value(item, chosen.key)
            identity = self.identity(item)
            if has_value:
                present.append((value, identity, item))
            else:
                absent.append((identity, item))
        present.sort(key=lambda row: row[1])
        absent.sort(key=lambda row: row[0])
        present.sort(key=lambda row: row[0], reverse=reverse)
        ordered = [row[2] for row in present] + [row[1] for row in absent]
        if favorites_first:
            favorites = []
            rest = []
            for item in ordered:
                if bool(read(item, ("favorite", "is_favorite", "favourite"))):
                    favorites.append(item)
                else:
                    rest.append(item)
            ordered = favorites + rest
        return ordered


__all__ = [
    "DEFAULT_SORT",
    "FIELDS",
    "MISSING",
    "SORTS",
    "SortKeys",
    "SortSpec",
    "fold",
    "natural_key",
    "normalize_sort",
    "parse_size",
    "parse_time",
    "read",
    "sort_entries",
    "spec",
    "value_for",
]
