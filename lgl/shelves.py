"""Custom shelves on the home page: a saved filter rendered as its own row."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

from . import content_filters

SHELVES_KEY = "home_shelves"
MAX_SHELVES = 8
DEFAULT_LIMIT = 6


def _store(settings: Any) -> MutableMapping[str, dict[str, Any]] | None:
    getter = getattr(settings, "get", None)
    if settings is None or not callable(getter):
        return None
    try:
        value = getter(SHELVES_KEY)
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, dict) else {}


def _save(settings: Any, mapping: MutableMapping[str, dict[str, Any]]) -> bool:
    setter = getattr(settings, "set", None)
    if not callable(setter):
        return False
    setter(SHELVES_KEY, dict(mapping))
    saver = getattr(settings, "save", None)
    if callable(saver):
        try:
            saver()
        except Exception:  # noqa: BLE001
            return False
    return True


def shelf_id(name: Any) -> str:
    text = "-".join(str(name or "").strip().lower().split())
    return "".join(char for char in text if char.isalnum() or char == "-")[:40]


def shelves(settings: Any) -> list[dict[str, Any]]:
    """Every configured shelf, in the order it was added."""
    mapping = _store(settings) or {}
    rows: list[dict[str, Any]] = []
    for key, raw in mapping.items():
        if not isinstance(raw, Mapping):
            continue
        rows.append(
            {
                "id": str(key),
                "name": str(raw.get("name") or key),
                "filters": content_filters.parse(raw.get("filters")),
                "limit": max(1, min(40, int(raw.get("limit") or DEFAULT_LIMIT))),
            }
        )
    return rows


def add(settings: Any, name: Any, filters: Mapping[str, Any] | None = None, limit: int = DEFAULT_LIMIT) -> str:
    """Add or replace a shelf; returns its id, or an empty string on refusal."""
    mapping = _store(settings)
    label = str(name or "").strip()
    key = shelf_id(label)
    if mapping is None or not key or (key not in mapping and len(mapping) >= MAX_SHELVES):
        return ""
    mapping[key] = {
        "name": label[:60],
        "filters": {name: value for name, value in dict(filters or {}).items()},
        "limit": max(1, min(40, int(limit or DEFAULT_LIMIT))),
    }
    _save(settings, mapping)
    return key


def remove(settings: Any, key: str) -> bool:
    mapping = _store(settings)
    if mapping is None or key not in mapping:
        return False
    mapping.pop(key, None)
    _save(settings, mapping)
    return True


def fill(shelf: Mapping[str, Any], items: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The first `limit` items matching the shelf's filter."""
    filters = shelf.get("filters")
    if not isinstance(filters, content_filters.Filters):
        filters = content_filters.parse(filters if isinstance(filters, Mapping) else None)
    limit = max(1, int(shelf.get("limit") or DEFAULT_LIMIT))
    return content_filters.apply(items, filters)[:limit]


def render(settings: Any, items: Iterable[Mapping[str, Any]]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    """``(shelf name, rows)`` for every shelf that has something to show."""
    pool = list(items)
    rows: list[tuple[str, list[Mapping[str, Any]]]] = []
    for shelf in shelves(settings):
        filled = fill(shelf, pool)
        if filled:
            rows.append((shelf["name"], filled))
    return rows


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_SHELVES",
    "SHELVES_KEY",
    "add",
    "fill",
    "remove",
    "render",
    "shelf_id",
    "shelves",
]
