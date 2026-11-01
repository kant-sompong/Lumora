"""Local library folders and inspection of downloaded files.

Nothing here executes a downloaded file.  It only reports what a file appears
to be so the user can review it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .filetypes import (
    classify_file,
    is_appimage,
    is_document,
    is_launchable_kind,
    is_shell,
    looks_like_elf,
    looks_like_shell,
)
from .util import (
    atomic_write_json,
    ensure_directory,
    is_within,
    validate_identifier,
)

MANIFEST_NAME = "game.json"
LAUNCH_LOG = "launch.log"


@dataclass(frozen=True)
class LocalFile:
    path: Path
    relpath: str
    size: int
    kind: str
    executable_candidate: bool

    @property
    def document(self) -> bool:
        return self.kind == "Document"

    @property
    def launchable(self) -> bool:
        return self.executable_candidate


@dataclass(frozen=True)
class GameFolder:
    path: Path
    files: list[LocalFile]
    executables: list[LocalFile]
    documents: list[LocalFile]


def game_folder(download_root: Path | str, identifier: str) -> Path:
    root = ensure_directory(download_root)
    validate_identifier(identifier)
    folder = ensure_directory(root / identifier)
    if not is_within(folder, root):
        raise ValueError("Refusing to use a game folder outside the download directory.")
    return folder


def write_manifest(folder: Path, item: Any) -> Path:
    """Store a small metadata snapshot beside the downloaded files.

    This is app-owned metadata; it does not modify downloaded payloads.
    """
    folder = folder.resolve()
    manifest = {
        "identifier": getattr(item, "identifier", ""),
        "title": getattr(item, "title", ""),
        "creator": getattr(item, "creator", ""),
        "date": getattr(item, "date", ""),
        "description": getattr(item, "description", ""),
        "licenseurl": getattr(item, "licenseurl", ""),
        "rights": getattr(item, "rights", ""),
        "subjects": getattr(item, "subjects", []),
        "source_url": getattr(item, "download_url", ""),
        "managed_by": "Native Game Library",
    }
    path = folder / MANIFEST_NAME
    atomic_write_json(path, manifest)
    return path


def inspect_file(path: Path) -> LocalFile:
    path = path.resolve()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    kind = classify_file(path.name)
    launchable = is_launchable_kind(path.name)
    # Magic bytes win over ambiguous suffixes such as ``.bin``.
    if size and not is_document(path.name) and not is_shell(path.name) and not is_appimage(path.name):
        if looks_like_elf(path):
            kind = "ELF executable"
            launchable = True
        elif kind == "File" and looks_like_shell(path):
            kind = "Shell script"
            launchable = True
    if is_appimage(path.name):
        kind = "AppImage"
        launchable = True
    if is_document(path.name):
        kind = "Document"
        launchable = False
    return LocalFile(
        path=path,
        relpath=str(path),
        size=size,
        kind=kind,
        executable_candidate=launchable,
    )


def scan_game_folder(folder: Path | str, max_files: int = 2000) -> GameFolder:
    folder = Path(folder).expanduser().resolve()
    files: list[LocalFile] = []
    if folder.is_dir():
        for current, dirs, names in os.walk(folder, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and not Path(current, d).is_symlink()
            )
            for name in sorted(names):
                if len(files) >= max_files:
                    break
                if name.startswith(".") or name.endswith(".part"):
                    continue
                if name in (MANIFEST_NAME, LAUNCH_LOG):
                    continue
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                local = inspect_file(path)
                files.append(local)
            if len(files) >= max_files:
                break
    executables = [f for f in files if f.executable_candidate]
    documents = [f for f in files if f.document]
    return GameFolder(path=folder, files=files, executables=executables, documents=documents)
