"""Multiple local profiles.

Roadmap section 18: separate preferences, favourites, collections and history per
profile, make the active profile obvious, define backup and migration, and never
imply account security that does not exist.

Lumora has no accounts and no encryption at rest, so this module says so plainly
in :data:`SECURITY_NOTE` and the UI repeats it.  Profiles are folders, not users.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROFILES_FILE = "profiles.json"
PROFILE_ROOT = "profiles"
DEFAULT_ID = "default"
DEFAULT_NAME = "Default"
MAX_PROFILES = 12
MAX_NAME = 40

SECURITY_NOTE = (
    "Profiles keep each person's library, favourites and history in separate folders. "
    "They are not accounts: anyone with access to this computer can open any profile, "
    "so profiles are not a security boundary."
)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    created: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "created": self.created}


def _clean_name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_NAME]


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "profile"
    candidate = base[:32]
    counter = 2
    while candidate in taken:
        candidate = f"{base[:28]}-{counter}"
        counter += 1
    return candidate


class Profiles:
    """The profile list, its active choice, and per-profile folders."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._items: list[Profile] = []
        self._active = DEFAULT_ID
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            raw = None
        items: list[Profile] = []
        if isinstance(raw, dict):
            for entry in raw.get("profiles") or []:
                if not isinstance(entry, dict):
                    continue
                name = _clean_name(entry.get("name"))
                ident = str(entry.get("id") or "").strip()
                if not name or not ident:
                    continue
                items.append(
                    Profile(
                        id=ident[:40],
                        name=name,
                        created=float(entry.get("created") or 0.0),
                    )
                )
        seen: set[str] = set()
        self._items = [item for item in items if not (item.id in seen or seen.add(item.id))]
        active = str(raw.get("active") or "") if isinstance(raw, dict) else ""
        self._active = active if any(item.id == active for item in self._items) else (
            self._items[0].id if self._items else DEFAULT_ID
        )
        if not self._items:
            self._items = [Profile(DEFAULT_ID, DEFAULT_NAME, 0.0)]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "active": self._active,
            "profiles": [item.as_dict() for item in self._items],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
        tmp.replace(self.path)

    # -- queries --------------------------------------------------------
    def list(self) -> list[Profile]:
        return list(self._items)

    def get(self, profile_id: str) -> Profile | None:
        for item in self._items:
            if item.id == profile_id:
                return item
        return None

    def active(self) -> Profile:
        return self.get(self._active) or self._items[0]

    @property
    def active_id(self) -> str:
        return self.active().id

    def label(self) -> str:
        active = self.active()
        return f"{active.name} ({len(self._items)} profile{'s' if len(self._items) != 1 else ''})"

    # -- changes --------------------------------------------------------
    def create(self, name: str) -> Profile | None:
        clean = _clean_name(name)
        if not clean or len(self._items) >= MAX_PROFILES:
            return None
        if any(item.name.casefold() == clean.casefold() for item in self._items):
            return None
        profile = Profile(_slug(clean, {item.id for item in self._items}), clean, time.time())
        self._items.append(profile)
        self.save()
        return profile

    def rename(self, profile_id: str, name: str) -> bool:
        clean = _clean_name(name)
        profile = self.get(profile_id)
        if profile is None or not clean:
            return False
        if any(item.name.casefold() == clean.casefold() and item.id != profile_id for item in self._items):
            return False
        index = self._items.index(profile)
        self._items[index] = Profile(profile.id, clean, profile.created)
        self.save()
        return True

    def remove(self, profile_id: str) -> bool:
        """Delete a profile folder only when another profile remains."""
        profile = self.get(profile_id)
        if profile is None or len(self._items) <= 1:
            return False
        self._items = [item for item in self._items if item.id != profile_id]
        if self._active == profile_id:
            self._active = self._items[0].id
        self.save()
        return True

    def activate(self, profile_id: str) -> bool:
        if self.get(profile_id) is None:
            return False
        self._active = profile_id
        self.save()
        return True

    # -- storage --------------------------------------------------------
    def data_dir(self, base: Path | str, profile_id: str | None = None) -> Path:
        target = self.get(profile_id or self.active_id) or self.active()
        return Path(base) / PROFILE_ROOT / target.id

    def export(self) -> dict[str, Any]:
        return {
            "version": 1,
            "active": self._active,
            "profiles": [item.as_dict() for item in self._items],
        }

    def import_(self, document: Any) -> int:
        """Restore profiles from a backup; returns how many were added."""
        if not isinstance(document, dict):
            return 0
        added = 0
        for entry in document.get("profiles") or []:
            if not isinstance(entry, dict):
                continue
            name = _clean_name(entry.get("name"))
            ident = str(entry.get("id") or "").strip()[:40]
            if not name or not ident or self.get(ident) is not None:
                continue
            if any(item.name.casefold() == name.casefold() for item in self._items):
                continue
            if len(self._items) >= MAX_PROFILES:
                break
            self._items.append(Profile(ident, name, float(entry.get("created") or 0.0)))
            added += 1
        if added:
            self.save()
        return added


def migrate(base: Path | str, legacy_names: Iterable[str] = ("library.json", "collections.json")) -> list[Path]:
    """Move single-profile files into the default profile folder.

    Runs once: files that are already inside a profile folder are left alone, and
    an existing destination is never overwritten.
    """
    root = Path(base)
    target = root / PROFILE_ROOT / DEFAULT_ID
    moved: list[Path] = []
    for name in legacy_names:
        source = root / name
        if not source.is_file():
            continue
        destination = target / name
        if destination.exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
        except OSError:
            continue
        moved.append(destination)
    return moved


__all__ = [
    "DEFAULT_ID",
    "DEFAULT_NAME",
    "MAX_NAME",
    "MAX_PROFILES",
    "PROFILES_FILE",
    "PROFILE_ROOT",
    "Profile",
    "Profiles",
    "SECURITY_NOTE",
    "migrate",
]
