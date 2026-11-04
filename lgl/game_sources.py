"""Installed-game sources, kept distinct from catalogue entries.

Roadmap section 16: evaluate integrations for installed-game libraries, clearly
distinguish installed games from catalogue rows, keep optional integrations off
until configured, never ask for third-party account credentials that Lumora
cannot use safely, and document the licensing limitations.

Nothing here talks to a storefront.  Each source is a folder the user points at;
the UI merges what it finds with the catalogue and labels the origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SETTINGS_PATHS = "game_source_paths"
SETTINGS_ENABLED = "game_sources_enabled"
INSTALLED = "installed"
CATALOGUE = "catalogue"


@dataclass(frozen=True)
class GameSource:
    key: str
    label: str
    requires_setup: bool
    installed: bool = True
    limitation: str = ""


SOURCES: tuple[GameSource, ...] = (
    GameSource(
        key="steam",
        label="Steam library folder",
        requires_setup=True,
        limitation="Reads the folder you choose; it does not sign in to Steam or read its account data.",
    ),
    GameSource(
        key="epic",
        label="Epic Games folder",
        requires_setup=True,
        limitation="Folder scanning only; no Epic account is used and no entitlement checks are made.",
    ),
    GameSource(
        key="gog",
        label="GOG folder",
        requires_setup=True,
        limitation="Folder scanning only; GOG Galaxy databases are not modified.",
    ),
    GameSource(
        key="local-folder",
        label="Any folder of games",
        requires_setup=True,
        limitation="Whatever you point it at; Lumora does not guess outside that folder.",
    ),
    GameSource(
        key="catalogue",
        label="Built-in catalogue",
        requires_setup=False,
        installed=False,
        limitation="Catalogue rows are titles Lumora knows about, not proof that anything is installed.",
    ),
)

_BY_KEY = {source.key: source for source in SOURCES}


def sources() -> tuple[GameSource, ...]:
    return SOURCES


def get(key: str) -> GameSource | None:
    return _BY_KEY.get(str(key or "").strip().lower())


def _paths(settings: Any) -> dict[str, str]:
    raw = settings.get(SETTINGS_PATHS) if settings is not None else None
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(v or "").strip()}


def _disabled(settings: Any) -> set[str]:
    raw = settings.get(SETTINGS_ENABLED) if settings is not None else None
    if not isinstance(raw, Mapping):
        return set()
    return {str(k) for k, v in raw.items() if v is False}


def path_for(settings: Any, key: str) -> Path | None:
    source = get(key)
    if source is None or not source.requires_setup:
        return None
    value = _paths(settings).get(source.key)
    return Path(value) if value else None


def set_path(settings: Any, key: str, path: Any) -> bool:
    source = get(key)
    if source is None or not source.requires_setup:
        return False
    text = str(path or "").strip()
    paths = _paths(settings)
    if not text:
        paths.pop(source.key, None)
    else:
        paths[source.key] = text
    settings.set(SETTINGS_PATHS, paths)
    return True


def is_enabled(settings: Any, key: str) -> bool:
    source = get(key)
    if source is None:
        return False
    if not source.requires_setup:
        return True
    return source.key not in _disabled(settings)


def set_enabled(settings: Any, key: str, enabled: bool) -> bool:
    source = get(key)
    if source is None or not source.requires_setup:
        return False
    raw = settings.get(SETTINGS_ENABLED) if settings is not None else None
    flags = {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, Mapping) else {}
    flags[source.key] = bool(enabled)
    settings.set(SETTINGS_ENABLED, flags)
    return True


@dataclass(frozen=True)
class SourceState:
    key: str
    label: str
    state: str
    path: Path | None
    limitation: str


def setup_state(settings: Any) -> list[SourceState]:
    out: list[SourceState] = []
    for source in SOURCES:
        path = path_for(settings, source.key)
        if not source.requires_setup:
            state = "available"
        elif not is_enabled(settings, source.key):
            state = "off"
        elif path is None:
            state = "needs-folder"
        elif not path.is_dir():
            state = "missing-folder"
        else:
            state = "ready"
        out.append(SourceState(source.key, source.label, state, path, source.limitation))
    return out


def classify(entry: Mapping[str, Any] | Any) -> str:
    """Is this row an installed game or a catalogue entry?"""
    def read(name: str, default: Any = None) -> Any:
        return entry.get(name, default) if isinstance(entry, Mapping) else getattr(entry, name, default)

    if read("installed", False):
        return INSTALLED
    source = str(read("source", "") or "").strip().lower()
    if source and source != "catalogue":
        return INSTALLED
    return CATALOGUE


def identity(entry: Mapping[str, Any] | Any) -> str:
    def read(name: str) -> str:
        value = entry.get(name) if isinstance(entry, Mapping) else getattr(entry, name, "")
        return str(value or "").strip().casefold()

    for name in ("identifier", "id", "title"):
        value = read(name)
        if value:
            return value
    return ""


def merge(
    installed: Iterable[Any],
    catalogue: Iterable[Any],
) -> tuple[list[dict[str, Any]], int]:
    """Merge two lists, letting the installed record win on a shared title."""
    merged: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for group, source in ((INSTALLED, installed), (CATALOGUE, catalogue)):
        for entry in source:
            key = identity(entry)
            if not key:
                continue
            record = dict(entry) if isinstance(entry, Mapping) else {"title": getattr(entry, "title", key)}
            record.setdefault("origin", group)
            if key in merged:
                duplicates += 1
                if group == INSTALLED:
                    merged[key] = record
                continue
            merged[key] = record
    ordered = sorted(merged.values(), key=lambda item: (str(item.get("origin")) != INSTALLED, str(item.get("title", "")).casefold()))
    return ordered, duplicates


def limitations() -> list[str]:
    return [
        "Storefront libraries are read from a folder you choose; no storefront API or account is used.",
        "Installed games and catalogue entries are shown separately so a catalogue row is never mistaken for an install.",
        "Lumora does not ask for Steam, Epic or GOG credentials, and cannot verify entitlements on your behalf.",
        "Some stores keep their manifests in undocumented formats; a folder can look empty until it is pointed at the right level.",
    ]


__all__ = [
    "CATALOGUE",
    "GameSource",
    "INSTALLED",
    "SOURCES",
    "SETTINGS_ENABLED",
    "SETTINGS_PATHS",
    "SourceState",
    "classify",
    "get",
    "identity",
    "is_enabled",
    "limitations",
    "merge",
    "path_for",
    "set_enabled",
    "set_path",
    "setup_state",
    "sources",
]
