"""Normalized, validated game metadata and provenance-aware merging.

Catalog / metadata providers are untrusted input.  They hand over plain
mappings, and this module is the only place that decides what a value means:

* every field is validated and bounded, so a provider cannot inject a path, a
  ``javascript:`` URL, or a 5 MB description into the library;
* a provider result records which fields it actually supplied, and which of
  those it flags as unverified;
* merging never silently overwrites a value the user edited.  The winning value
  and the losing conflict are both reported to the caller so the UI can show
  them.

Field provenance lives in ``LibraryEntry.metadata_fields`` as
``{field: provider}``, where ``"user"`` means "typed by hand, never overwrite".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from .util import strip_html

#: Fields a provider may contribute.  Anything else in an incoming mapping is
#: ignored, so a provider cannot write ``folder``, ``status``, or ``favorite``.
METADATA_FIELDS = (
    "title",
    "creator",
    "year",
    "developer",
    "publisher",
    "genres",
    "platforms",
    "release_date",
    "description",
    "version",
    "source_url",
)
LIST_FIELDS = frozenset({"genres", "platforms"})
#: Values typed by the user are protected from provider merges.
USER_PROVIDER = "user"

MAX_TEXT = 200
MAX_DESCRIPTION = 4000
MAX_LIST_ITEMS = 12
MAX_LIST_ITEM = 40
MAX_URL = 500

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


class MetadataError(ValueError):
    """An invalid metadata value that the caller must fix."""


def clean_text(value: Any, limit: int = MAX_TEXT) -> str:
    """Single-line text: no control characters, no runs of whitespace."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFC", value)
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    return text[:limit].strip()


def clean_description(value: Any) -> str:
    """Plain-text description; any HTML a provider sends is flattened."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple)):
        value = "\n\n".join(str(item) for item in value)
    if not isinstance(value, str):
        return ""
    text = strip_html(value)
    # Keep paragraph breaks; only real control characters are removed.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_DESCRIPTION]


def clean_year(value: Any) -> str:
    """A plausible four-digit release year, else ``""``."""
    text = clean_text(value, 32)
    match = re.search(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b", text)
    return match.group(1) if match else ""


def clean_release_date(value: Any) -> str:
    """An ISO-style date (``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``) or ``""``."""
    text = clean_text(value, 32)
    match = _DATE.match(text)
    if not match:
        return ""
    year, month, day = match.group(1), match.group(2), match.group(3)
    if not 1890 <= int(year) <= 2199:
        return ""
    if month is not None and not 1 <= int(month) <= 12:
        return ""
    if day is not None and not 1 <= int(day) <= 31:
        return ""
    return text


def clean_url(value: Any) -> str:
    """An ``http(s)`` URL without credentials, or ``""``."""
    text = clean_text(value, MAX_URL)
    if not text:
        return ""
    try:
        split = urlsplit(text)
    except ValueError:
        return ""
    if split.scheme.lower() not in ("http", "https"):
        return ""
    if not split.netloc or split.username or split.password:
        return ""
    if any(char.isspace() for char in text):
        return ""
    return text


def clean_list(value: Any) -> tuple[str, ...]:
    """A short, de-duplicated, order-preserving list of labels."""
    if value is None or isinstance(value, bool):
        return ()
    items: Iterable[Any]
    if isinstance(value, str):
        items = re.split(r"[,;/|]", value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = value
    else:
        return ()
    kept: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            # Numbers, mappings, and nested lists are never labels.
            continue
        text = clean_text(item, MAX_LIST_ITEM)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(text)
        if len(kept) >= MAX_LIST_ITEMS:
            break
    return tuple(kept)


def clean_field(name: str, value: Any) -> Any:
    """Validate one field by name; unknown fields raise :class:`MetadataError`."""
    if name not in METADATA_FIELDS:
        raise MetadataError(f"Unknown metadata field: {name}")
    if name == "description":
        return clean_description(value)
    if name == "year":
        return clean_year(value)
    if name == "release_date":
        return clean_release_date(value)
    if name == "source_url":
        return clean_url(value)
    if name in LIST_FIELDS:
        return clean_list(value)
    return clean_text(value)


def _empty_for(name: str) -> Any:
    return () if name in LIST_FIELDS else ""


@dataclass
class GameMetadata:
    """One provider's normalized view of a game.

    Empty fields mean "the provider did not say", never "there is no value".
    ``unverified`` names fields the provider itself is unsure about.
    """

    title: str = ""
    creator: str = ""
    year: str = ""
    developer: str = ""
    publisher: str = ""
    genres: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    release_date: str = ""
    description: str = ""
    version: str = ""
    source_url: str = ""
    provider: str = ""
    unverified: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_mapping(cls, data: Any, *, provider: str = "") -> "GameMetadata":
        """Build from an arbitrary mapping, ignoring anything unusable."""
        if not isinstance(data, Mapping):
            return cls(provider=clean_text(provider, 60))
        values: dict[str, Any] = {}
        for name in METADATA_FIELDS:
            raw = data.get(name)
            if raw is None:
                continue
            try:
                values[name] = clean_field(name, raw)
            except MetadataError:  # pragma: no cover - names are ours
                continue
        name = data.get("provider")
        declared = clean_text(provider or (name if isinstance(name, str) else ""), 60)
        raw_unverified = data.get("unverified")
        unverified = frozenset(
            item for item in (raw_unverified or ())
            if isinstance(item, str) and item in METADATA_FIELDS
        )
        return cls(provider=declared, unverified=unverified, **values)

    def provided(self) -> dict[str, Any]:
        """Only the fields carrying a value, in canonical order."""
        out: dict[str, Any] = {}
        for name in METADATA_FIELDS:
            value = getattr(self, name)
            if value:
                out[name] = value
        return out

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name for name in METADATA_FIELDS if not getattr(self, name)
        )


@dataclass
class MergeReport:
    """What a merge did, field by field."""

    applied: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    #: Fields the incoming metadata simply did not carry.
    ignored: list[str] = field(default_factory=list)
    #: Fields that were supplied but flagged unverified by the provider.
    unverified: list[str] = field(default_factory=list)
    provider: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    def summary(self) -> str:
        if not self.applied and not self.conflicts:
            return "No new metadata was available."
        bits = []
        if self.applied:
            bits.append(f"{len(self.applied)} field(s) updated")
        if self.conflicts:
            bits.append(f"{len(self.conflicts)} kept your value")
        return ", ".join(bits) + "."


def _current(entry: Any, name: str) -> Any:
    value = getattr(entry, name, _empty_for(name))
    if name in LIST_FIELDS:
        return tuple(value or ())
    return value if isinstance(value, str) else ""


def merge_into(
    entry: Any,
    incoming: GameMetadata,
    *,
    provider: str = "",
    overwrite_user: bool = False,
) -> MergeReport:
    """Apply *incoming* to *entry* without clobbering values it must not touch.

    Rules, in order:

    1. a field the provider did not supply is ignored;
    2. a field flagged unverified by the provider is recorded but not written;
    3. a field the user edited wins unless ``overwrite_user`` is set (that flag
       exists for explicit user edits, not for provider refreshes);
    4. a non-empty value from a *different* provider is a conflict: the existing
       value stays and the conflict is reported.

    The entry keeps ``metadata_fields`` (``{field: provider}``) up to date and
    ``metadata_provider`` records the last provider that contributed.
    """
    name_of_provider = clean_text(provider or incoming.provider, 60)
    report = MergeReport(provider=name_of_provider)
    provenance = getattr(entry, "metadata_fields", None)
    if not isinstance(provenance, dict):
        provenance = {}
        setattr(entry, "metadata_fields", provenance)

    for name in METADATA_FIELDS:
        value = getattr(incoming, name)
        if not value:
            report.ignored.append(name)
            continue
        if name in incoming.unverified:
            report.unverified.append(name)
            continue
        current = _current(entry, name)
        source = provenance.get(name, "")
        if source == USER_PROVIDER and not overwrite_user:
            if current != value:
                report.conflicts.append(name)
            continue
        allowed = (
            not current
            or current == value
            or source in ("", name_of_provider)
            or (overwrite_user and source == USER_PROVIDER)
        )
        if not allowed:
            # Two sources disagree: keep the earlier answer and say so.
            report.conflicts.append(name)
            continue
        if current != value:
            setattr(entry, name, value)
            report.applied.append(name)
        if name_of_provider and (source != USER_PROVIDER or overwrite_user):
            provenance[name] = name_of_provider

    if report.applied and name_of_provider:
        setattr(entry, "metadata_provider", name_of_provider)
    return report


def apply_user_edits(entry: Any, values: Mapping[str, Any]) -> MergeReport:
    """Apply validated edits typed by the user and mark them as user-authored.

    Raises :class:`MetadataError` for an unknown field or an invalid value so the
    dialog can point at the offending field instead of silently dropping it.
    """
    cleaned: dict[str, Any] = {}
    for name, value in values.items():
        if name not in METADATA_FIELDS:
            raise MetadataError(f"Unknown metadata field: {name}")
        cleaned[name] = clean_field(name, value)

    provenance = getattr(entry, "metadata_fields", None)
    if not isinstance(provenance, dict):
        provenance = {}
        setattr(entry, "metadata_fields", provenance)

    report = MergeReport(provider=USER_PROVIDER)
    for name, value in cleaned.items():
        if _current(entry, name) != value:
            setattr(entry, name, value)
            report.applied.append(name)
        provenance[name] = USER_PROVIDER
    return report


def provenance_of(entry: Any, name: str) -> str:
    provenance = getattr(entry, "metadata_fields", {})
    if isinstance(provenance, Mapping):
        value = provenance.get(name, "")
        return value if isinstance(value, str) else ""
    return ""


def note_user_field(entry: Any, name: str) -> None:
    """Record that the user authored one field (used by non-dialog edits)."""
    if name not in METADATA_FIELDS:
        raise MetadataError(f"Unknown metadata field: {name}")
    provenance = getattr(entry, "metadata_fields", None)
    if not isinstance(provenance, dict):
        provenance = {}
        setattr(entry, "metadata_fields", provenance)
    provenance[name] = USER_PROVIDER


__all__ = [
    "GameMetadata",
    "LIST_FIELDS",
    "MAX_DESCRIPTION",
    "METADATA_FIELDS",
    "MergeReport",
    "MetadataError",
    "USER_PROVIDER",
    "apply_user_edits",
    "clean_description",
    "clean_field",
    "clean_list",
    "clean_release_date",
    "clean_text",
    "clean_url",
    "clean_year",
    "merge_into",
    "note_user_field",
    "provenance_of",
]
