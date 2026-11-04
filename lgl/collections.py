"""Named collections over the game library.

A collection is a list of library identifiers plus, optionally, a saved filter
that makes it smart: a smart collection recomputes its members every time it is
opened instead of freezing whatever matched on the day it was created.  Manual
collections keep their order, because "the order I put them in" is often the
whole point of making one.

The store is its own file next to ``library.json``, so a damaged collection list
can never take the library index with it and a backup can restore one without
the other.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .util import atomic_write_json, read_json

COLLECTIONS_FILENAME = "collections.json"
SCHEMA_VERSION = 1
MAX_NAME = 80
MAX_MEMBERS = 5000
#: Filters a smart collection may store.  They are the keyword arguments of
#: ``LibraryStore.filter_entries``, so a smart collection cannot ask for
#: something the library cannot answer.
SMART_KEYS = (
    "query",
    "status",
    "favorite",
    "compatibility",
    "year_from",
    "year_to",
    "genre",
    "games_only",
    "exclude_adult",
)


def _clean_name(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME]


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return slug or "collection"


def clean_filters(raw: Any) -> dict[str, Any]:
    """Keep only the filters the library understands, with usable types."""
    if not isinstance(raw, Mapping):
        return {}
    cleaned: dict[str, Any] = {}
    for key in SMART_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key in ("year_from", "year_to"):
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                continue
        elif key in ("games_only", "exclude_adult"):
            cleaned[key] = bool(value)
        elif key == "favorite":
            if value is None:
                continue
            cleaned[key] = bool(value)
        else:
            text = _clean_name(value)
            if text:
                cleaned[key] = text
    return cleaned


@dataclass
class Collection:
    id: str
    name: str
    items: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    filters: dict[str, Any] | None = None

    @property
    def smart(self) -> bool:
        return bool(self.filters)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "items": list(self.items),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.filters:
            data["filters"] = dict(self.filters)
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> "Collection | None":
        if not isinstance(raw, Mapping):
            return None
        name = _clean_name(raw.get("name"))
        if not name:
            return None
        identifier = _clean_name(raw.get("id")) or _slug(name)
        items: list[str] = []
        raw_items = raw.get("items")
        if isinstance(raw_items, list):
            for item in raw_items:
                text = str(item or "").strip()
                if text and text not in items:
                    items.append(text)
        return cls(
            id=identifier,
            name=name,
            items=items[:MAX_MEMBERS],
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0),
            filters=clean_filters(raw.get("filters")) or None,
        )


class CollectionStore:
    """Collections persisted to ``collections.json``.

    ``path=None`` keeps the store in memory, which is what tests and previews
    want; nothing here touches the library index or any downloaded file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else None
        self._collections: dict[str, Collection] = {}
        if self.path is not None:
            self.load()

    # -- persistence ------------------------------------------------------

    def load(self) -> list[Collection]:
        self._collections = {}
        if self.path is None:
            return []
        data = read_json(self.path, None)
        if not isinstance(data, Mapping):
            return []
        raw_items = data.get("collections")
        if not isinstance(raw_items, list):
            return []
        for raw in raw_items:
            collection = Collection.from_dict(raw)
            if collection is None or collection.id in self._collections:
                continue
            self._collections[collection.id] = collection
        return self.all()

    def save(self) -> bool:
        if self.path is None:
            return False
        atomic_write_json(self.path, self.document())
        return True

    def document(self) -> dict[str, Any]:
        """The exact document :meth:`save` writes, for backups."""
        return {
            "version": SCHEMA_VERSION,
            "collections": [item.to_dict() for item in self.all()],
        }

    # -- reading ----------------------------------------------------------

    def all(self) -> list[Collection]:
        return sorted(
            self._collections.values(),
            key=lambda item: (item.name.casefold(), item.id),
        )

    def get(self, collection_id: str) -> Collection | None:
        return self._collections.get(str(collection_id or "").strip())

    def by_name(self, name: str) -> Collection | None:
        wanted = _clean_name(name).casefold()
        for collection in self._collections.values():
            if collection.name.casefold() == wanted:
                return collection
        return None

    def members(self, collection_id: str) -> list[str]:
        collection = self.get(collection_id)
        return list(collection.items) if collection else []

    def containing(self, identifier: str) -> list[str]:
        """Names of every manual collection holding ``identifier``."""
        wanted = str(identifier or "").strip()
        return [
            collection.name
            for collection in self.all()
            if not collection.smart and wanted in collection.items
        ]

    def resolve(
        self,
        collection_id: str,
        entries: Sequence[Any],
        *,
        filterer: Callable[..., list[Any]] | None = None,
    ) -> list[Any]:
        """Library entries for one collection, in the order it defines.

        Smart collections call ``filterer`` (``LibraryStore.filter_entries`` by
        default) so the membership rule lives in one place instead of being
        re-implemented here.
        """
        collection = self.get(collection_id)
        if collection is None:
            return []
        if collection.smart:
            if filterer is None:
                return []
            try:
                return list(filterer(entries, **collection.filters))
            except TypeError:
                return list(filterer(list(entries)))
        order = {identifier: index for index, identifier in enumerate(collection.items)}
        matched = [
            entry
            for entry in entries
            if str(getattr(entry, "identifier", "") or "") in order
        ]
        matched.sort(key=lambda entry: order[str(getattr(entry, "identifier", ""))])
        return matched

    # -- writing ----------------------------------------------------------

    def _unique_id(self, name: str) -> str:
        base = _slug(name)
        candidate = base
        suffix = 2
        while candidate in self._collections:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def create(self, name: str) -> Collection:
        label = _clean_name(name)
        if not label:
            raise ValueError("A collection needs a name.")
        existing = self.by_name(label)
        if existing is not None:
            raise ValueError(f"A collection called {existing.name!r} already exists.")
        stamp = time.time()
        collection = Collection(
            id=self._unique_id(label), name=label, created_at=stamp, updated_at=stamp
        )
        self._collections[collection.id] = collection
        self.save()
        return collection

    def rename(self, collection_id: str, name: str) -> bool:
        collection = self.get(collection_id)
        label = _clean_name(name)
        if collection is None or not label:
            return False
        clash = self.by_name(label)
        if clash is not None and clash.id != collection.id:
            raise ValueError(f"A collection called {clash.name!r} already exists.")
        if collection.name == label:
            return False
        collection.name = label
        collection.updated_at = time.time()
        self.save()
        return True

    def delete(self, collection_id: str) -> bool:
        collection = self._collections.pop(str(collection_id or ""), None)
        if collection is None:
            return False
        self.save()
        return True

    def add(self, collection_id: str, identifiers: Sequence[str]) -> int:
        """Add identifiers, keeping existing order.  Returns how many were added."""
        collection = self.get(collection_id)
        if collection is None:
            return 0
        added = 0
        for identifier in identifiers:
            text = str(identifier or "").strip()
            if not text or text in collection.items:
                continue
            if len(collection.items) >= MAX_MEMBERS:
                break
            collection.items.append(text)
            added += 1
        if added:
            collection.updated_at = time.time()
            self.save()
        return added

    def remove(self, collection_id: str, identifiers: Sequence[str]) -> int:
        collection = self.get(collection_id)
        if collection is None:
            return 0
        unwanted = {str(item or "").strip() for item in identifiers}
        kept = [item for item in collection.items if item not in unwanted]
        removed = len(collection.items) - len(kept)
        if removed:
            collection.items = kept
            collection.updated_at = time.time()
            self.save()
        return removed

    def move(self, collection_id: str, identifier: str, index: int) -> bool:
        """Move one member to ``index`` (clamped), which is manual ordering."""
        collection = self.get(collection_id)
        wanted = str(identifier or "").strip()
        if collection is None or wanted not in collection.items:
            return False
        current = collection.items.index(wanted)
        target = max(0, min(len(collection.items) - 1, int(index)))
        if target == current:
            return False
        collection.items.pop(current)
        collection.items.insert(target, wanted)
        collection.updated_at = time.time()
        self.save()
        return True

    def set_filters(self, collection_id: str, filters: Mapping[str, Any] | None) -> bool:
        """Turn a collection smart, or update the rule that defines it."""
        collection = self.get(collection_id)
        if collection is None:
            return False
        cleaned = clean_filters(filters)
        collection.filters = cleaned or None
        collection.updated_at = time.time()
        self.save()
        return True

    def clear_filters(self, collection_id: str) -> bool:
        return self.set_filters(collection_id, None)


__all__ = [
    "COLLECTIONS_FILENAME",
    "MAX_MEMBERS",
    "MAX_NAME",
    "SCHEMA_VERSION",
    "SMART_KEYS",
    "Collection",
    "CollectionStore",
    "clean_filters",
]
