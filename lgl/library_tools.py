"""Library housekeeping: tags, bulk edits, duplicate detection and the small
set of fields a user may correct by hand."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

TAGS_KEY = "library_tags"
TAG_LIMIT = 24

#: Fields the edit sheet may change, and the label shown for each.
EDITABLE = (
    ("title", "Title"),
    ("description", "Description"),
    ("year", "Year"),
    ("poster", "Poster"),
)

_WS = re.compile(r"\s+")


def clean_tag(value: Any) -> str:
    """Lower-case, single-spaced, comma-free tag text."""
    text = _WS.sub(" ", str(value or "").replace(",", " ")).strip().lower()
    return text[:32]


def key_for(entry: Mapping[str, Any]) -> str:
    return str(entry.get("key") or entry.get("identifier") or entry.get("title") or "").strip().casefold()


def _store(settings: Any) -> MutableMapping[str, list[str]] | None:
    if settings is None:
        return None
    getter = getattr(settings, "get", None)
    if not callable(getter):
        return None
    try:
        value = getter(TAGS_KEY)
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, dict) else {}


def _save(settings: Any, mapping: MutableMapping[str, list[str]]) -> bool:
    setter = getattr(settings, "set", None)
    if not callable(setter):
        return False
    setter(TAGS_KEY, {key: sorted(set(value)) for key, value in mapping.items() if value})
    saver = getattr(settings, "save", None)
    if callable(saver):
        try:
            saver()
        except Exception:  # noqa: BLE001
            return False
    return True


def tags_for(settings: Any, entry: str | Mapping[str, Any]) -> tuple[str, ...]:
    key = key_for(entry) if isinstance(entry, Mapping) else str(entry or "").strip().casefold()
    mapping = _store(settings) or {}
    return tuple(sorted(str(tag) for tag in mapping.get(key, [])))


def add_tag(settings: Any, entry: str | Mapping[str, Any], tag: Any) -> tuple[str, ...]:
    key = key_for(entry) if isinstance(entry, Mapping) else str(entry or "").strip().casefold()
    cleaned = clean_tag(tag)
    mapping = _store(settings)
    if not key or not cleaned or mapping is None:
        return tags_for(settings, key)
    current = {str(value) for value in mapping.get(key, [])}
    current.add(cleaned)
    mapping[key] = sorted(current)[:TAG_LIMIT]
    _save(settings, mapping)
    return tuple(mapping[key])


def remove_tag(settings: Any, entry: str | Mapping[str, Any], tag: Any) -> tuple[str, ...]:
    key = key_for(entry) if isinstance(entry, Mapping) else str(entry or "").strip().casefold()
    cleaned = clean_tag(tag)
    mapping = _store(settings)
    if not key or mapping is None or key not in mapping:
        return ()
    remaining = [value for value in mapping.get(key, []) if str(value) != cleaned]
    if remaining:
        mapping[key] = sorted(set(remaining))
    else:
        mapping.pop(key, None)
    _save(settings, mapping)
    return tuple(mapping.get(key, []))


def tag_counts(entries: Iterable[Mapping[str, Any]], settings: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        for tag in tags_for(settings, entry):
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def _normal(value: Any) -> str:
    return _WS.sub(" ", str(value or "").strip().casefold())


def duplicates(entries: Iterable[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Groups of entries that look like the same title."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        title = _normal(entry.get("title"))
        if not title:
            continue
        size = entry.get("size")
        bucket = f"{title}|{size if size is not None else ''}"
        groups.setdefault(bucket, []).append(entry)
    return [group for group in groups.values() if len(group) > 1]


def bulk_update(
    entries: Iterable[Mapping[str, Any]],
    chosen: Iterable[str],
    field: str,
    value: Any,
) -> tuple[list[dict[str, Any]], int]:
    """Return copies with `field` set on the chosen keys, plus how many changed."""
    if field not in {name for name, _label in EDITABLE}:
        return [dict(entry) for entry in entries], 0
    wanted = {str(key).strip().casefold() for key in chosen if str(key).strip()}
    changed = 0
    result: list[dict[str, Any]] = []
    for entry in entries:
        copy = dict(entry)
        if key_for(entry) in wanted:
            copy[field] = value
            changed += 1
        result.append(copy)
    return result, changed


def apply_edit(entry: Mapping[str, Any], field: str, value: Any) -> dict[str, Any]:
    """One edited copy of `entry`, with values normalised per field."""
    if field not in {name for name, _label in EDITABLE}:
        return dict(entry)
    copy = dict(entry)
    if field == "year":
        try:
            copy["year"] = int(str(value).strip()[:4])
        except (TypeError, ValueError):
            return copy
    elif field == "title":
        text = _WS.sub(" ", str(value or "")).strip()
        if text:
            copy["title"] = text
    else:
        copy[field] = str(value or "").strip()
    return copy


__all__ = [
    "EDITABLE",
    "TAGS_KEY",
    "TAG_LIMIT",
    "add_tag",
    "apply_edit",
    "bulk_update",
    "clean_tag",
    "duplicates",
    "key_for",
    "remove_tag",
    "tag_counts",
    "tags_for",
]
