"""Backup and restore for the things a user cannot easily rebuild.

A backup is a zip with one JSON document per part and a manifest that records a
SHA-256 per part, so a truncated or hand-edited archive is refused instead of
half-restored.  API keys are stripped on the way in: the settings document is
the one file that can hold credentials, and a backup is exactly the kind of file
people email to themselves.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .util import atomic_write_json

BACKUP_FORMAT = "lumora-backup"
BACKUP_VERSION = 1
MANIFEST_NAME = "manifest.json"
PARTS = ("library", "collections", "settings", "history")
DEFAULT_PARTS = ("library", "collections", "settings")
#: Settings keys that must never leave the machine in a backup.
SECRET_KEYS = ("api_keys",)
_MAX_PART_BYTES = 64 * 1024 * 1024


class BackupError(Exception):
    """The archive is not a usable Lumora backup."""


@dataclass
class BackupReport:
    ok: bool = False
    path: str = ""
    parts: list[str] = field(default_factory=list)
    removed_secrets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class BackupInfo:
    """What a backup claims to contain, without applying any of it."""

    ok: bool = False
    parts: dict[str, int] = field(default_factory=dict)
    created_at: float = 0.0
    app_version: str = ""
    errors: list[str] = field(default_factory=list)


def sanitize_settings(
    document: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Copy a settings document without the keys that carry credentials."""
    if not isinstance(document, Mapping):
        return {}, []
    cleaned = {key: value for key, value in document.items() if key not in SECRET_KEYS}
    removed = [key for key in SECRET_KEYS if key in document]
    return cleaned, removed


def describe_format() -> str:
    """One paragraph a user can read before trusting a backup file."""
    return (
        "A backup is a zip archive. manifest.json lists every part with its size and SHA-256, "
        "followed by library.json, collections.json, settings.json and history.json. "
        "API keys are stripped from settings.json and never written. Restoring verifies every "
        "hash before touching anything, and refuses parts written by a newer format version."
    )


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def create_backup(
    destination: Path,
    *,
    library: Mapping[str, Any] | None = None,
    collections: Mapping[str, Any] | None = None,
    settings: Mapping[str, Any] | None = None,
    history: Any = None,
    include: Iterable[str] = DEFAULT_PARTS,
    app_version: str = "",
) -> BackupReport:
    """Write ``destination`` and report exactly what went into it.

    A part is written only when the caller supplied it, so a user who asks for
    "library and collections" gets a small archive rather than empty documents
    that would overwrite real data on restore.
    """
    report = BackupReport(path=str(destination))
    wanted = tuple(part for part in include if part in PARTS)
    documents: dict[str, Any] = {}
    if "library" in wanted and library is not None:
        documents["library"] = library
    if "collections" in wanted and collections is not None:
        documents["collections"] = collections
    if "settings" in wanted and settings is not None:
        cleaned, removed = sanitize_settings(settings)
        documents["settings"] = cleaned
        report.removed_secrets = removed
    if "history" in wanted and history is not None:
        documents["history"] = {"entries": history}

    if not documents:
        report.errors.append("Nothing was selected to back up.")
        return report

    path = Path(destination)
    entries: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    for name, document in documents.items():
        try:
            payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
        except (TypeError, ValueError) as exc:
            report.errors.append(f"{name} could not be serialised: {exc}")
            return report
        payloads[name] = payload
        entries.append({"name": name, "bytes": len(payload), "sha256": _digest(payload)})

    manifest = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": time.time(),
        "app_version": str(app_version or ""),
        "parts": entries,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
            for name, payload in payloads.items():
                archive.writestr(f"{name}.json", payload)
    except OSError as exc:
        report.errors.append(f"Could not write the backup: {exc}")
        return report

    report.ok = True
    report.parts = list(payloads)
    return report


def _manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError as exc:
        raise BackupError("The archive has no manifest.") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("The manifest is not readable JSON.") from exc
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        raise BackupError("The archive is not a Lumora backup.")
    return data


def inspect(source: Path) -> BackupInfo:
    """Verify an archive without touching any existing data."""
    info = BackupInfo()
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = _manifest(archive)
            parts = manifest.get("parts")
            if not isinstance(parts, list):
                info.errors.append("The manifest lists no parts.")
                return info
            for entry in parts:
                if not isinstance(entry, Mapping):
                    info.errors.append("A manifest entry is malformed.")
                    continue
                name = str(entry.get("name") or "")
                expected = str(entry.get("sha256") or "")
                try:
                    payload = archive.read(f"{name}.json")
                except KeyError:
                    info.errors.append(f"{name} is listed but missing.")
                    continue
                if len(payload) > _MAX_PART_BYTES:
                    info.errors.append(f"{name} is implausibly large.")
                    continue
                if _digest(payload) != expected:
                    info.errors.append(f"{name} does not match its checksum.")
                    continue
                info.parts[name] = len(payload)
            info.created_at = float(manifest.get("created_at") or 0.0)
            info.app_version = str(manifest.get("app_version") or "")
    except (OSError, zipfile.BadZipFile) as exc:
        info.errors.append(f"The file cannot be read as a zip: {exc}")
        return info
    except BackupError as exc:
        info.errors.append(str(exc))
        return info
    info.ok = bool(info.parts) and not info.errors
    return info


def load(source: Path, *, parts: Iterable[str] | None = None) -> dict[str, Any]:
    """Return validated documents keyed by part name.

    Raises :class:`BackupError` when any requested part is missing or damaged,
    so a caller never merges half an archive into a working library.
    """
    info = inspect(source)
    if not info.ok:
        raise BackupError("; ".join(info.errors) or "The backup is not usable.")
    wanted = tuple(parts) if parts is not None else tuple(info.parts)
    documents: dict[str, Any] = {}
    with zipfile.ZipFile(source) as archive:
        for name in wanted:
            if name not in info.parts:
                raise BackupError(f"The backup has no {name} part.")
            try:
                documents[name] = json.loads(archive.read(f"{name}.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError(f"{name} is not readable JSON.") from exc
    return documents


def restore_plan(
    existing: Mapping[str, bool], documents: Mapping[str, Any]
) -> dict[str, str]:
    """Describe what a restore would do before it does it.

    ``existing`` maps part name to whether this installation already has data
    there; anything present is reported as an overwrite so the caller can warn.
    """
    return {
        name: ("overwrite" if existing.get(name) else "add") for name in documents
    }


def write_document(path: Path, document: Mapping[str, Any]) -> None:
    """Write one restored document atomically."""
    atomic_write_json(Path(path), dict(document))


__all__ = [
    "describe_format",

    "BACKUP_FORMAT",
    "BACKUP_VERSION",
    "DEFAULT_PARTS",
    "MANIFEST_NAME",
    "PARTS",
    "SECRET_KEYS",
    "BackupError",
    "BackupInfo",
    "BackupReport",
    "create_backup",
    "inspect",
    "load",
    "restore_plan",
    "sanitize_settings",
    "write_document",
]
