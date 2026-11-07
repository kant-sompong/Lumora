"""Library housekeeping: tags, bulk edits, duplicate detection and the small
set of fields a user may correct by hand."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
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
    key = _key_of(entry) if not isinstance(entry, str) else str(entry or "").strip().casefold()
    mapping = _store(settings) or {}
    return tuple(sorted(str(tag) for tag in mapping.get(key, [])))


def add_tag(settings: Any, entry: str | Mapping[str, Any], tag: Any) -> tuple[str, ...]:
    key = _key_of(entry) if not isinstance(entry, str) else str(entry or "").strip().casefold()
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
    key = _key_of(entry) if not isinstance(entry, str) else str(entry or "").strip().casefold()
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


# ---------------------------------------------------------------------------
# Bulk actions over a selection
# ---------------------------------------------------------------------------

#: Metadata fields a bulk edit may set, and the label shown for each.
BULK_FIELDS = (
    ("year", "Year"),
    ("genre", "Genre"),
    ("developer", "Developer"),
    ("publisher", "Publisher"),
)

BULK_ACTIONS = ("tag", "collection", "favorite", "field")


def _key_of(entry: Any) -> str:
    """Store key for an entry object or a plain mapping."""
    if isinstance(entry, Mapping):
        return key_for(entry)
    return str(
        getattr(entry, "identifier", "") or getattr(entry, "key", "") or ""
    ).strip().casefold()


def _title_of(entry: Any) -> str:
    if isinstance(entry, Mapping):
        title = entry.get("title") or entry.get("key") or ""
    else:
        title = getattr(entry, "title", "") or getattr(entry, "identifier", "")
    return str(title or "").strip() or "Untitled"


def field_value(entry: Any, field_name: str) -> Any:
    """The entry's current value for a :data:`BULK_FIELDS` name."""
    if field_name == "genre":
        if isinstance(entry, Mapping):
            return tuple(str(value) for value in entry.get("genres") or ())
        return tuple(str(value) for value in getattr(entry, "genres", ()) or ())
    if isinstance(entry, Mapping):
        return entry.get(field_name, "")
    return getattr(entry, field_name, "")


def set_field_value(entry: Any, field_name: str, value: Any) -> Any:
    """Write one bulk field onto an entry object (mappings are left alone)."""
    if isinstance(entry, Mapping):
        return entry
    attribute = "genres" if field_name == "genre" else field_name
    setattr(entry, attribute, value)
    return entry


def clean_bulk_value(field_name: str, value: Any) -> tuple[Any, str]:
    """Normalise a bulk value, or return ``(value, reason)`` with a reason."""
    field_name = (field_name or "").strip().casefold()
    if field_name == "year":
        text = str(value if value is not None else "").strip()[:4]
        if not text.isdigit() or not 1000 <= int(text) <= 2999:
            return "", "year is not a number"
        return text, ""
    if field_name == "genre":
        names: list[str] = []
        for part in str(value or "").split(","):
            name = clean_tag(part)
            if name and name not in names:
                names.append(name)
        if not names:
            return (), "no value given"
        return tuple(names)[:4], ""
    text = _WS.sub(" ", str(value or "")).strip()[:80]
    if not text:
        return "", "no value given"
    return text, ""


def _display(value: Any) -> str:
    if isinstance(value, (tuple, list)):
        return ", ".join(str(item) for item in value)
    return str(value)


@dataclass(frozen=True)
class BulkChange:
    """What a bulk action does, before it is matched to any game.

    ``value`` is the tag, collection name, or new field value; ``field`` is
    the edited metadata field, or the collection id for a collection change;
    ``remove`` says whether membership is being taken away instead of added.
    """

    action: str
    value: Any = ""
    field: str = ""
    remove: bool = False

    @property
    def label(self) -> str:
        if self.action == "tag":
            verb = "Remove the tag" if self.remove else "Add the tag"
            return f'{verb} “{self.value}”'
        if self.action == "collection":
            verb = "Remove from" if self.remove else "Add to"
            return f'{verb} the collection “{self.value}”'
        if self.action == "favorite":
            return "Unfavorite" if self.remove else "Mark as favorite"
        written = dict(BULK_FIELDS).get(self.field, self.field.title())
        return f'Set {written} to “{_display(self.value)}”'


@dataclass
class BulkPlan:
    """Which selected games change, which are left alone, and why.

    Every change keeps its previous value, so :meth:`undo` can hand back the
    exact inverse without consulting the library again.
    """

    change: BulkChange
    #: ``(identifier, previous value, wanted value)`` for each game that changes.
    changes: list[tuple[str, Any, Any]] = field(default_factory=list)
    #: ``(title, reason)`` for each selected game the action cannot change.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Id of a collection this plan created, so its undo can remove it again.
    created_collection: str = ""

    @property
    def updated(self) -> int:
        return len(self.changes)

    @property
    def total(self) -> int:
        return len(self.changes) + len(self.skipped)

    @property
    def title(self) -> str:
        return self.change.label

    def reasons(self) -> str:
        """Distinct skip reasons, in the order they were first hit."""
        seen: list[str] = []
        for _title, reason in self.skipped:
            if reason not in seen:
                seen.append(reason)
        return "; ".join(seen)

    def summary(self) -> str:
        """The result line shown after the action, e.g. ``12 updated, 1 skipped: reason``."""
        text = f"{self.updated} updated"
        if self.skipped:
            text += f", {len(self.skipped)} skipped: {self.reasons()}"
        return text

    def confirm(self) -> str:
        """The question asked before anything is written."""
        noun = "game" if self.total == 1 else "games"
        detail = f"{self.updated} of {self.total} {noun} will change"
        if self.skipped:
            detail += f"; {len(self.skipped)} will be left alone"
        return f"{self.title} — {detail}."

    def undo(self) -> BulkPlan:
        """The plan that puts every changed value back."""
        return BulkPlan(
            change=self.change,
            changes=[
                (identifier, wanted, previous)
                for identifier, previous, wanted in self.changes
            ],
            created_collection=self.created_collection,
        )


class BulkHistory:
    """The last bulk action, held in memory so it can be undone once."""

    def __init__(self) -> None:
        self._last: BulkPlan | None = None

    @property
    def last(self) -> BulkPlan | None:
        return self._last

    def can_undo(self) -> bool:
        return self._last is not None and bool(self._last.changes)

    def remember(self, plan: BulkPlan) -> None:
        if plan.changes:
            self._last = plan

    def forget(self) -> None:
        self._last = None

    def undo(self) -> BulkPlan | None:
        plan = self._last
        self._last = None
        if plan is None or not plan.changes:
            return None
        return plan.undo()


def _picked(
    entries: Iterable[Any], chosen: Iterable[Any]
) -> list[tuple[str, Any, str]]:
    """``(key, entry, title)`` for every selected key the library still has."""
    wanted = {str(key).strip().casefold() for key in chosen if str(key).strip()}
    picked: list[tuple[str, Any, str]] = []
    for entry in entries:
        key = _key_of(entry)
        if key and key in wanted:
            picked.append((key, entry, _title_of(entry)))
    return picked


def plan_tags(
    entries: Iterable[Any],
    chosen: Iterable[Any],
    tag: Any,
    *,
    tags_of: Callable[[Any], Sequence[str]],
    remove: bool = False,
) -> BulkPlan:
    """Plan adding (or removing) one tag on every selected game.

    ``tags_of(entry)`` is the caller's tag reader, so this stays pure and the
    GTK and Qt frontends can share it.
    """
    cleaned = clean_tag(tag)
    plan = BulkPlan(BulkChange("tag", cleaned, remove=bool(remove)))
    picked = _picked(entries, chosen)
    if not cleaned:
        plan.skipped = [(title, "no tag given") for _key, _entry, title in picked]
        return plan
    for key, entry, title in picked:
        current = {str(value).strip().casefold() for value in tags_of(entry) or ()}
        has = cleaned in current
        wants = not remove
        if has == wants:
            plan.skipped.append((title, "already tagged" if wants else "not tagged"))
            continue
        plan.changes.append((key, has, wants))
    return plan


def plan_collection(
    entries: Iterable[Any],
    chosen: Iterable[Any],
    name: Any,
    *,
    collection_id: str = "",
    is_member: Callable[[Any], bool],
    remove: bool = False,
) -> BulkPlan:
    """Plan adding selected games to a collection (or taking them out)."""
    label = _WS.sub(" ", str(name or "")).strip()[:80]
    plan = BulkPlan(
        BulkChange("collection", label, collection_id, remove=bool(remove))
    )
    picked = _picked(entries, chosen)
    if not label:
        plan.skipped = [
            (title, "no collection given") for _key, _entry, title in picked
        ]
        return plan
    for key, entry, title in picked:
        inside = bool(is_member(entry))
        wants = not remove
        if inside == wants:
            plan.skipped.append(
                (
                    title,
                    "already in this collection" if wants else "not in this collection",
                )
            )
            continue
        plan.changes.append((key, inside, wants))
    return plan


def plan_favorite(
    entries: Iterable[Any], chosen: Iterable[Any], value: bool
) -> BulkPlan:
    """Plan marking the selection as favorite (``value``) or unfavorite."""
    wanted = bool(value)
    plan = BulkPlan(BulkChange("favorite", wanted, remove=not wanted))
    for key, entry, title in _picked(entries, chosen):
        current = bool(field_value(entry, "favorite"))
        if current == wanted:
            plan.skipped.append(
                (title, "already a favorite" if wanted else "not a favorite")
            )
            continue
        plan.changes.append((key, current, wanted))
    return plan


def plan_field(
    entries: Iterable[Any],
    chosen: Iterable[Any],
    field_name: str,
    value: Any,
) -> BulkPlan:
    """Plan setting one metadata field on every selected game."""
    field_name = (field_name or "").strip().casefold()
    wanted, reason = clean_bulk_value(field_name, value)
    plan = BulkPlan(BulkChange("field", wanted, field_name))
    picked = _picked(entries, chosen)
    if field_name not in {name for name, _label in BULK_FIELDS}:
        plan.skipped = [
            (title, "not a field that can be bulk edited")
            for _key, _entry, title in picked
        ]
        return plan
    if reason:
        plan.skipped = [(title, reason) for _key, _entry, title in picked]
        return plan
    for key, entry, title in picked:
        current = field_value(entry, field_name)
        if current == wanted:
            plan.skipped.append((title, "already that value"))
            continue
        plan.changes.append((key, current, wanted))
    return plan


def apply_plan(
    plan: BulkPlan,
    *,
    settings: Any = None,
    store: Any = None,
    collections_store: Any = None,
) -> int:
    """Write a plan through the existing stores; returns how many were written.

    Only the library index, its tags in settings, and the collection file are
    touched.  Downloaded files and their folders are never renamed, moved, or
    deleted.
    """
    written = 0
    getter = getattr(store, "get", None)
    for key, _previous, wanted in plan.changes:
        entry = None
        if callable(getter):
            try:
                entry = getter(key)
            except Exception:  # noqa: BLE001 - a missing entry is not fatal
                entry = None
        if plan.change.action == "tag":
            if wanted:
                add_tag(settings, key, plan.change.value)
            else:
                remove_tag(settings, key, plan.change.value)
        elif plan.change.action == "favorite" and apply_favorite(store, entry, key, wanted):
            pass
        elif plan.change.action == "collection" and collections_store is not None:
            if wanted:
                collections_store.add(plan.change.field, [key])
            else:
                collections_store.remove(plan.change.field, [key])
        elif plan.change.action == "field" and entry is not None:
            set_field_value(entry, plan.change.field, wanted)
        written += 1

    if written:
        saver = getattr(store, "save", None)
        if callable(saver):
            try:
                saver()
            except OSError:
                pass
    if plan.created_collection and collections_store is not None:
        # Undoing a collection action that created the collection must not
        # leave an empty one behind.
        collection = collections_store.get(plan.created_collection)
        if collection is not None and not collection.items:
            collections_store.delete(plan.created_collection)
    return written


def apply_favorite(store: Any, entry: Any, key: str, value: bool) -> bool:
    """Set one favorite in the store and on the entry object it returned."""
    setter = getattr(store, "set_favorite", None)
    if callable(setter):
        try:
            setter(key, bool(value))
        except Exception:  # noqa: BLE001 - the entry copy still gets the value
            return False
    if entry is not None:
        try:
            entry.favorite = bool(value)
        except Exception:  # noqa: BLE001 - read-only stand-ins are fine
            return False
    return True


__all__ = [
    "BULK_ACTIONS",
    "BULK_FIELDS",
    "EDITABLE",
    "TAGS_KEY",
    "TAG_LIMIT",
    "BulkChange",
    "BulkHistory",
    "BulkPlan",
    "add_tag",
    "apply_edit",
    "apply_favorite",
    "apply_plan",
    "bulk_update",
    "clean_bulk_value",
    "clean_tag",
    "duplicates",
    "field_value",
    "key_for",
    "plan_collection",
    "plan_favorite",
    "plan_field",
    "plan_tags",
    "remove_tag",
    "set_field_value",
    "tag_counts",
    "tags_for",
]
