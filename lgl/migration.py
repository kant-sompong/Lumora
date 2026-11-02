"""One-time copy of settings and library data into the Lumora folders.

Until 0.5.0 the application stored everything under ``linux-game-library``
(Linux) or ``NativeGameLibrary`` (Windows).  0.6.0 renamed both, so on the
first launch after the upgrade the old documents are **copied** to the new
folders:

* the old folders are never modified, moved or deleted, so an older build
  keeps working and rolling back is simply deleting the new folders;
* nothing already present in the new folders is overwritten, so a user who has
  run the new build and then reopens an old one cannot lose the newer data;
* only the small JSON documents are copied.  The image cache is regenerable
  artwork and can be several hundred megabytes, so it is left behind and
  rebuilt on demand.

Every launch calls :func:`migrate_once`; after the first successful copy a
marker file records what happened, which makes the call a no-op afterwards.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from . import platform_support
from .util import atomic_write_json, read_json

#: Written into the new configuration folder after a migration.
MARKER_NAME = "migration.json"
#: Cache folders are regenerated, never copied.
_SKIP_DIR_NAMES = frozenset({"cache", "caches", "thumbnails", "imagecache"})


@dataclass
class MigrationReport:
    """What a :func:`migrate_once` call did, for logs and for the UI."""

    migrated: bool = False
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reason: str = ""

    def summary(self) -> str:
        if not self.migrated:
            return self.reason or "Nothing to migrate."
        count = len(self.copied)
        plural = "file" if count == 1 else "files"
        text = f"Copied {count} {plural} from the previous Lumora folders."
        if self.errors:
            text += f" {len(self.errors)} could not be copied."
        return text


def marker_path() -> Path:
    return platform_support.config_dir() / MARKER_NAME


def _rollback_hint(
    system: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    return (
        "To go back to the previous build, close Lumora and delete "
        f"{platform_support.config_dir(system, env)} and "
        f"{platform_support.data_dir(system, env)}. "
        "The old folders were left untouched, so the previous version keeps "
        "its settings, library and queue."
    )


def _copy_entry(
    source: Path,
    target: Path,
    copied: list[str],
    skipped: list[str],
    errors: list[str],
    label: str,
) -> None:
    """Copy one file or folder, never overwriting an existing target."""
    name = f"{label}/{source.name}"
    if target.exists() or target.is_symlink():
        skipped.append(name)
        return
    temporary: Path | None = None
    try:
        if source.is_dir():
            if source.name.casefold() in _SKIP_DIR_NAMES:
                skipped.append(name)
                return
            shutil.copytree(source, target, symlinks=False)
        else:
            # Copy beside the target and rename, so an interrupted launch
            # cannot leave a half-written settings or library document.
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".migrating")
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
    except OSError as exc:
        errors.append(f"{name}: {exc}")
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        return
    copied.append(name)


def _migrate_dir(
    source: Path,
    target: Path,
    copied: list[str],
    skipped: list[str],
    errors: list[str],
    label: str,
) -> None:
    if not source.is_dir():
        return
    try:
        entries = sorted(source.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        errors.append(f"{source}: {exc}")
        return
    for entry in entries:
        if entry.is_symlink():
            skipped.append(f"{label}/{entry.name}")
            continue
        _copy_entry(entry, target / entry.name, copied, skipped, errors, label)


def migrate_once(
    *,
    system: str | None = None,
    env: Mapping[str, str] | None = None,
) -> MigrationReport:
    """Copy the pre-0.6.0 documents into the Lumora folders, once.

    Safe to call on every launch: it does nothing after the first successful
    run, and it never raises for filesystem problems (they are reported in
    ``errors`` so the caller can show one line instead of crashing).
    """
    report = MigrationReport()
    env = os.environ if env is None else env
    config = platform_support.config_dir(system, env)
    data = platform_support.data_dir(system, env)
    legacy_config = platform_support.legacy_config_dir(system, env)
    legacy_data = platform_support.legacy_data_dir(system, env)

    if marker_path_for(system, env).exists():
        report.reason = "Already migrated."
        return report
    if (config / "config.json").exists() or (data / "library.json").exists():
        report.reason = (
            "The Lumora folders already hold settings or a library, so the old "
            "folders were left alone."
        )
        _write_marker(system, env, report)
        return report
    if not legacy_config.is_dir() and not legacy_data.is_dir():
        report.reason = "No folders from an older version were found."
        return report

    _migrate_dir(legacy_config, config, report.copied, report.skipped,
                 report.errors, "config")
    _migrate_dir(legacy_data, data, report.copied, report.skipped,
                 report.errors, "data")
    report.migrated = True
    _write_marker(system, env, report)
    return report


def marker_path_for(
    system: str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    return platform_support.config_dir(system, env) / MARKER_NAME


def _write_marker(
    system: str | None, env: Mapping[str, str], report: MigrationReport
) -> None:
    """Record the migration so the next launch is a no-op.

    A marker that cannot be written only costs one extra (harmless) check on
    the next start, so failure here is not an error for the user.
    """
    document = {
        "version": 1,
        "status": "copied" if report.migrated else "skipped",
        "reason": report.reason,
        "from": {
            "config": str(platform_support.legacy_config_dir(system, env)),
            "data": str(platform_support.legacy_data_dir(system, env)),
        },
        "to": {
            "config": str(platform_support.config_dir(system, env)),
            "data": str(platform_support.data_dir(system, env)),
        },
        "at": time.time(),
        "copied": report.copied,
        "skipped": report.skipped,
        "errors": report.errors,
        "rollback": _rollback_hint(system, env),
    }
    try:
        atomic_write_json(marker_path_for(system, env), document)
    except OSError:
        return


def last_report(
    system: str | None = None, env: Mapping[str, str] | None = None
) -> dict:
    """Return the recorded migration, or an empty mapping."""
    data = read_json(marker_path_for(system, env), {})
    return data if isinstance(data, dict) else {}
