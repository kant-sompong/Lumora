"""Persistent, read-only local-library metadata store.

This module keeps a small JSON index of games found under a download root.  It
never executes, moves, or deletes downloaded files; the only file it ever
writes is its own JSON document (atomically).  All helpers are standard
library only and take :class:`pathlib.Path` values so the code stays portable.

The store is deliberately forgiving: malformed JSON or malformed entry fields
must never raise, because library data may have been edited or truncated by
another tool.  Callers that want strict validation should validate before
importing.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import magnet as magnetlib
from . import metadata as meta
from .library import MANIFEST_NAME, scan_game_folder
from .util import atomic_write_json, read_json

try:  # human_size is small and dependency-free, but stay defensive.
    from .util import human_size
except Exception:  # pragma: no cover - util always provides it in practice
    def human_size(num: int | float | None) -> str:  # type: ignore[misc]
        return "—" if num is None else str(num)


#: 2 added optional metadata, provenance, play history, and magnet fields.
#: Documents written by version 1 still load unchanged: every new field has a
#: safe default, so an old index needs no rewrite to be read.
SCHEMA_VERSION = 2

STATUSES = ("downloaded", "installed", "incomplete", "failed", "missing")
DEFAULT_STATUS = "downloaded"

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_MAX_HASH_BYTES = 64 * 1024 * 1024


# --------------------------------------------------------------------------
# Tolerant value conversion
# --------------------------------------------------------------------------

def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        try:
            return max(0, int(value))
        except (OverflowError, ValueError):
            return default
    if isinstance(value, str):
        try:
            return max(0, int(value.strip()))
        except (TypeError, ValueError):
            return default
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (OverflowError, ValueError):
            return default
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return default
    return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    return default


def _as_year(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if isinstance(value, float):
        try:
            number = int(value)
        except (OverflowError, ValueError):
            return ""
        return str(number) if number > 0 else ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if len(text) >= 4 and text[:4].isdigit():
            return text[:4]
        return text[:16]
    return ""


def _year_int(value: Any) -> int | None:
    text = _as_year(value)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _title_key(entry: "LibraryEntry") -> str:
    """Normalised title used to spot the same game downloaded twice."""
    title = " ".join(str(entry.title or "").casefold().split())
    return title or f"id:{entry.identifier.casefold()}"


#: Natural direction per sort key.  Dates and sizes read best newest/largest
#: first; titles read best A→Z.  ``recent`` means last seen, ``played`` last
#: launched.  Anything not listed here sorts as ``title`` ascending.
SORT_DEFAULT_DESCENDING: dict[str, bool] = {
    "title": False,
    "recent": True,
    "added": True,
    "played": True,
    "size": True,
    "year": True,
    "developer": False,
    "genre": False,
    "platform": False,
}


def default_descending(key: str) -> bool:
    """Direction a sort mode starts in when the user has not chosen one."""
    return SORT_DEFAULT_DESCENDING.get((key or "").casefold(), False)


#: Tags that mark an entry as adult.  Nothing in the catalog carries a rating
#: field, so this is a metadata-keyword heuristic, not a content check: it only
#: reads what a user or a provider actually wrote in genres, notes or a title.
ADULT_TAGS = (
    "nsfw",
    "adult",
    "18+",
    "hentai",
    "ecchi",
    "erotic",
    "eroge",
    "porn",
    "pornographic",
    "lewd",
    "xxx",
)


def looks_adult(entry: "LibraryEntry") -> bool:
    """True when the entry's own metadata carries an adult tag.

    Genres are matched exactly (so ``Adult`` tags hit, ``Adventure`` does not);
    titles and notes are token-matched for the unambiguous markers only.  This
    is a best-effort hide, never a guarantee about a game's content.
    """
    for genre in entry.genres or ():
        if str(genre).strip().casefold() in ADULT_TAGS:
            return True
    haystack = f" {str(entry.notes or '').casefold()} {str(entry.title or '').casefold()} "
    for tag in ("nsfw", "18+", "xxx"):
        if f" {tag} " in haystack or f"[{tag}" in haystack or f"({tag}" in haystack:
            return True
    return False


def _developer_key(entry: "LibraryEntry") -> str:
    """Developer, falling back to the creator field older entries use."""
    return " ".join(str(entry.developer or entry.creator or "").casefold().split())


def _genre_key(entry: "LibraryEntry") -> str:
    """The alphabetically first genre, so multi-genre entries group once."""
    tags = [str(tag).strip().casefold() for tag in entry.genres or ()
            if str(tag).strip()]
    return min(tags) if tags else ""


def _platform_key(entry: "LibraryEntry") -> str:
    tags = [str(tag).strip().casefold() for tag in entry.platforms or ()
            if str(tag).strip()]
    return min(tags) if tags else ""


def _sort_missing(entry: "LibraryEntry", key: str) -> int:
    """1 when ``entry`` has no value for ``key``, else 0.

    Used as the final, always-ascending pass so unknown metadata lands at the
    bottom whichever way the primary key points.
    """
    if key == "title":
        return 0 if str(entry.title or "").strip() else 1
    if key == "developer":
        return 0 if _developer_key(entry) else 1
    if key == "genre":
        return 0 if _genre_key(entry) else 1
    if key == "platform":
        return 0 if _platform_key(entry) else 1
    if key == "size":
        return 0 if _as_int(entry.size) > 0 else 1
    if key == "year":
        return 0 if _year_int(entry.year) else 1
    if key == "added":
        return 0 if _as_float(entry.added_at) > 0 else 1
    if key == "played":
        return 0 if _as_float(entry.last_played) > 0 else 1
    return 0 if _as_float(entry.last_seen) > 0 else 1


_STATUS_RANK = {"installed": 3, "downloaded": 2, "incomplete": 1, "failed": 1}


def _copy_rank(entry: "LibraryEntry") -> tuple[int, int, float]:
    """Prefer a working, larger, more recently seen copy as the visible one."""
    status = _STATUS_RANK.get(entry.status, 0)
    try:
        size = int(entry.size or 0)
    except (TypeError, ValueError):
        size = 0
    try:
        seen = float(entry.last_seen or 0.0)
    except (TypeError, ValueError):
        seen = 0.0
    return (status, size, seen)


#: Entry fields a folder scan must carry over untouched.  Everything else is
#: recomputed from the folder, but user metadata, play history, and magnet
#: links belong to the entry, not to the scan.
_SCAN_PRESERVED = (
    "developer",
    "publisher",
    "genres",
    "platforms",
    "release_date",
    "description",
    "version",
    "metadata_provider",
    "last_played",
    "magnet",
    "infohash",
    "magnet_origin",
)
#: Fields a scan derives from the folder or manifest.  A user-authored value
#: wins over what the scan would otherwise write.
_SCAN_DERIVED = ("title", "creator", "year", "source_url")


def _preserve_extras(entry: "LibraryEntry", existing: "LibraryEntry") -> None:
    for name in _SCAN_PRESERVED:
        setattr(entry, name, getattr(existing, name))
    entry.metadata_fields = dict(existing.metadata_fields)
    for name in _SCAN_DERIVED:
        if existing.metadata_fields.get(name) == meta.USER_PROVIDER:
            setattr(entry, name, getattr(existing, name))


def _provenance(raw: Any) -> dict[str, str]:
    """Keep only known metadata fields with a short provider name."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for name in meta.METADATA_FIELDS:
        value = raw.get(name)
        if isinstance(value, str) and value.strip():
            out[name] = meta.clean_text(value, 60)
    return out


def _magnet_state(data: Mapping[str, Any]) -> tuple[str, str, str]:
    """Stored magnet text, verified infohash, and how it was obtained.

    The raw text is preserved even when it does not validate, so a hand-edited
    index never loses what the user typed; only the infohash/origin are cleared.
    """
    raw = _as_str(data.get("magnet")).strip()[: magnetlib.MAX_MAGNET_LENGTH]
    link = magnetlib.magnet_from_entry(
        raw, data.get("infohash"), data.get("magnet_origin")
    )
    if link is None:
        return raw, "", ""
    return (raw or link.uri), link.infohash, link.origin


def _is_within(path: Path, root: Path) -> bool:
    try:
        p = path.resolve()
        r = root.resolve()
        return p == r or r in p.parents
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------

@dataclass
class LibraryEntry:
    """One game folder tracked by the library."""

    identifier: str = ""
    title: str = ""
    creator: str = ""
    year: str = ""
    folder: str = ""
    size: int = 0
    file_count: int = 0
    favorite: bool = False
    status: str = DEFAULT_STATUS
    compatibility: str = ""
    integrity: str = ""
    cover_path: str = ""
    added_at: float = 0.0
    last_seen: float = 0.0
    source_url: str = ""
    notes: str = ""
    #: False for folders that clearly are not games (tools, docs, stray data).
    #: Detection is a heuristic, so the UI offers a way to show everything.
    is_game: bool = True
    # -- optional metadata (every field may be missing) --------------------
    developer: str = ""
    publisher: str = ""
    genres: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    release_date: str = ""
    description: str = ""
    version: str = ""
    #: Provider that last contributed metadata ("user", "archive.org", ...).
    metadata_provider: str = ""
    #: ``{field: provider}`` - which source supplied each metadata field.
    metadata_fields: dict[str, str] = field(default_factory=dict)
    #: Last launch request that the OS accepted, as a unix timestamp.
    last_played: float = 0.0
    #: Magnet link as supplied (or built from an infohash) and its normalized
    #: infohash.  Neither is ever guessed from a title.
    magnet: str = ""
    infohash: str = ""
    #: ``extracted``, ``constructed``, or empty.  Never conflated.
    magnet_origin: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "creator": self.creator,
            "year": self.year,
            "folder": self.folder,
            "size": self.size,
            "file_count": self.file_count,
            "favorite": self.favorite,
            "status": self.status,
            "compatibility": self.compatibility,
            "integrity": self.integrity,
            "cover_path": self.cover_path,
            "added_at": self.added_at,
            "last_seen": self.last_seen,
            "source_url": self.source_url,
            "notes": self.notes,
            "is_game": self.is_game,
            "developer": self.developer,
            "publisher": self.publisher,
            "genres": list(self.genres),
            "platforms": list(self.platforms),
            "release_date": self.release_date,
            "description": self.description,
            "version": self.version,
            "metadata_provider": self.metadata_provider,
            "metadata_fields": dict(self.metadata_fields),
            "last_played": self.last_played,
            "magnet": self.magnet,
            "infohash": self.infohash,
            "magnet_origin": self.magnet_origin,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "LibraryEntry":
        """Build an entry from arbitrary data without ever raising."""
        if not isinstance(data, Mapping):
            data = {}
        status = _as_str(data.get("status")).strip()
        if status not in STATUSES:
            status = DEFAULT_STATUS
        magnet_text, infohash, magnet_origin = _magnet_state(data)
        return cls(
            identifier=_as_str(data.get("identifier")).strip(),
            title=_as_str(data.get("title")),
            creator=_as_str(data.get("creator")),
            year=_as_year(data.get("year")),
            folder=_as_str(data.get("folder")),
            size=_as_int(data.get("size")),
            file_count=_as_int(data.get("file_count")),
            favorite=_as_bool(data.get("favorite")),
            status=status,
            compatibility=_as_str(data.get("compatibility")),
            integrity=_as_str(data.get("integrity")),
            cover_path=_as_str(data.get("cover_path")),
            added_at=_as_float(data.get("added_at")),
            last_seen=_as_float(data.get("last_seen")),
            source_url=meta.clean_url(data.get("source_url")),
            notes=_as_str(data.get("notes"))[:4000],
            is_game=_as_bool(data.get("is_game"), True),
            developer=meta.clean_text(data.get("developer")),
            publisher=meta.clean_text(data.get("publisher")),
            genres=meta.clean_list(data.get("genres")),
            platforms=meta.clean_list(data.get("platforms")),
            release_date=meta.clean_release_date(data.get("release_date")),
            description=meta.clean_description(data.get("description")),
            version=meta.clean_text(data.get("version"), 60),
            metadata_provider=meta.clean_text(data.get("metadata_provider"), 60),
            metadata_fields=_provenance(data.get("metadata_fields")),
            last_played=_as_float(data.get("last_played")),
            magnet=magnet_text,
            infohash=infohash,
            magnet_origin=magnet_origin,
        )

    @property
    def display_subtitle(self) -> str:
        bits: list[str] = []
        if self.year:
            bits.append(str(self.year))
        if self.size:
            bits.append(human_size(self.size))
        if self.compatibility:
            bits.append(self.compatibility)
        return " · ".join(bits)


@dataclass
class ScanReport:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    missing: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class DuplicateGroup:
    key: str
    kind: str
    paths: list[str]
    size: int


@dataclass
class ImportReport:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Filesystem helpers (read-only)
# --------------------------------------------------------------------------

def _walk_visible(folder: Path) -> Iterable[Path]:
    """Yield non-hidden regular files, never following symlinks."""
    try:
        for current, dirs, names in os.walk(folder, followlinks=False):
            dirs[:] = sorted(
                d for d in dirs
                if not d.startswith(".") and not Path(current, d).is_symlink()
            )
            for name in sorted(names):
                if name.startswith("."):
                    continue
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                yield path
    except OSError:
        return


def _has_visible_file(folder: Path) -> bool:
    for _ in _walk_visible(folder):
        return True
    return False


def _has_part_file(folder: Path) -> bool:
    return any(path.name.endswith(".part") for path in _walk_visible(folder))


_GAME_DIR_NAMES = {"game", "bin", "build", "data", "content", "assets"}
_GAME_SUFFIXES = (
    ".exe", ".msi", ".appimage", ".jar", ".sh", ".bash", ".run",
    ".iso", ".zip", ".7z", ".rar", ".tar", ".gz", ".xz",
)

# Installer/updater payloads say nothing about the folder being a game: a
# downloaded Hamachi or driver installer is a lone ``setup.exe``, not a game.
_INSTALLER_TOKENS = frozenset({
    "setup", "install", "installer", "uninstall", "uninstaller", "unins",
    "uninst", "update", "updater", "patch", "patcher", "redist",
    "redistributable", "runtime", "prereq", "prerequisites", "dxsetup",
    "directx", "dotnet", "vcredist", "bootstrapper",
})

# Desktop tools that are commonly dropped into a downloads folder and look
# executable but are not games.  Only used when nothing else backs the folder.
_TOOL_TOKENS = frozenset({
    "hamachi", "logmein", "radmin", "tailscale", "wireguard", "openvpn",
    "zerotier", "anydesk", "teamviewer", "putty", "winscp", "filezilla",
})


def _name_tokens(name: str) -> set[str]:
    """Lower-case word tokens of a file's base name (``Setup-2.exe`` -> setup, 2)."""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return {token for token in re.split(r"[^a-z0-9]+", base) if token}


def _installer_like(name: str) -> bool:
    return bool(_name_tokens(name) & _INSTALLER_TOKENS)


def _tool_like(name: str) -> bool:
    return bool(_name_tokens(name) & _TOOL_TOKENS)


def _looks_like_game(folder: Path, files: Sequence[Any], has_manifest: bool) -> bool:
    """Heuristic: does this folder look like a game rather than stray data?

    A metadata manifest is decisive.  A launchable payload is decisive too
    unless it is only an installer/updater (``setup.exe``) or a known desktop
    tool (Hamachi and friends).  Otherwise a game-like subdirectory or an
    archive payload counts.  Plain document/photo folders, tool installers and
    empty directories are not games.
    """
    if has_manifest:
        return True
    archive_payload = False
    tool_only = False
    for local in files:
        name = str(getattr(local, "path", ""))
        if _installer_like(name):
            continue
        if _tool_like(name):
            tool_only = True
            continue
        if getattr(local, "executable_candidate", False):
            return True
        kind = str(getattr(local, "kind", ""))
        if kind in ("ELF executable", "AppImage", "Shell installer"):
            return True
        if kind == "Archive":
            archive_payload = True
        if name.replace("\\", "/").rsplit("/", 1)[-1].lower().endswith(_GAME_SUFFIXES):
            return True
    if tool_only:
        return False
    for name in _GAME_DIR_NAMES:
        try:
            if (folder / name).is_dir():
                return True
        except OSError:
            continue
    return archive_payload


_COVER_TOKENS = {
    "cover": 100,
    "poster": 100,
    "boxart": 98,
    "capsule": 94,
    "appicon": 96,
    "logo": 84,
    "icon": 70,
    "thumbnail": 45,
    "thumb": 40,
    "splash": 28,
}

_COVER_DIR_TOKENS = {
    "cover": 30,
    "covers": 30,
    "artwork": 26,
    "art": 20,
    "support": 24,
    "metadata": 22,
    "meta": 8,
    "icons": 12,
    "image": 4,
    "images": 4,
}

# Game folders are full of textures, sprite sheets, and UI atlases.  Those are
# never a cover, however good they look at thumbnail size.
_BAD_COVER_TOKENS = frozenset({
    "sprite", "sprites", "sheet", "sheets", "atlas", "tileset", "tiles",
    "tile", "screenshot", "screenshots", "screen", "preview", "background",
    "bg", "wallpaper", "cursor", "font", "button", "buttons", "handle",
    "checkmark", "panel", "null", "mobile", "controls",
})


def _image_size(path: Path) -> tuple[int, int] | None:
    """Read an image's pixel size from its header, without decoding it."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:2] == b"BM" and len(head) >= 26:
                width, height = struct.unpack("<ii", head[18:26])
                return abs(width), abs(height)
            if head[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    marker = handle.read(2)
                    if len(marker) < 2:
                        return None
                    if marker[0] != 0xFF:
                        continue
                    code = marker[1]
                    if code in (0xD8, 0xD9) or 0xD0 <= code <= 0xD7:
                        continue
                    length_bytes = handle.read(2)
                    if len(length_bytes) < 2:
                        return None
                    length = struct.unpack(">H", length_bytes)[0]
                    if code in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        data = handle.read(5)
                        if len(data) < 5:
                            return None
                        height, width = struct.unpack(">HH", data[1:5])
                        return width, height
                    handle.seek(max(0, length - 2), 1)
    except (OSError, struct.error, ValueError):
        return None
    return None


def _cover_score(path: Path, folder: Path) -> int | None:
    """How strongly a file name says "this is the game's artwork"."""
    try:
        rel = path.relative_to(folder)
    except ValueError:
        rel = Path(path.name)
    stem = rel.stem.casefold()
    compact = re.sub(r"[^a-z0-9]", "", stem)
    tokens = {token for token in re.split(r"[^a-z0-9]+", stem) if token}
    if tokens & _BAD_COVER_TOKENS:
        return None
    score = 0
    for token, value in _COVER_TOKENS.items():
        if token in tokens:
            score = max(score, value)
    if compact.startswith("appicon") or "appicon" in compact:
        score = max(score, 96)
    if score == 0:
        return None
    for part in (p.casefold() for p in rel.parts[:-1]):
        for token, value in _COVER_DIR_TOKENS.items():
            if token in part:
                score += value
    return score - 8 * max(0, len(rel.parts) - 1)


def _find_cover(folder: Path, files: Sequence[Any]) -> str:
    """Pick deliberate artwork, never a random texture or sprite sheet.

    Only files whose names say cover/poster/icon/logo count.  When no file is
    convincing, an empty string is returned and the UI draws a generated
    monogram cover instead.
    """
    best: tuple[int, int, str, Path] | None = None
    for local in files:
        path = getattr(local, "path", None)
        if path is None:
            continue
        path = Path(path)
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        score = _cover_score(path, folder)
        if score is None or score <= 0:
            continue
        size = _image_size(path)
        if size is not None and (size[0] < 48 or size[1] < 48):
            continue
        area = size[0] * size[1] if size is not None else 0
        candidate = (score, area, str(path).casefold(), path)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    return str(best[3]) if best is not None else ""


def _compat_status(folder: Path) -> str:
    """Best-effort compatibility lookup; unavailable modules are ignored."""
    try:
        from .compatibility import analyze_folder

        result = analyze_folder(folder)
        status = getattr(result, "status", "")
        return status if isinstance(status, str) else ""
    except Exception:
        return ""


def _manifest_year(manifest: Mapping[str, Any]) -> str:
    for key in ("year", "date"):
        if key in manifest:
            year = _as_year(manifest.get(key))
            if year:
                return year
    return ""


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

class LibraryStore:
    """Metadata index for a local game library.

    ``path=None`` keeps the store purely in memory, which makes it safe for
    tests and for callers that only need scanning results.  Nothing in this
    class deletes, renames, or writes downloaded files.
    """

    def __init__(
        self,
        path: Path | None = None,
        download_root: Path | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path is not None else None
        self.download_root = (
            Path(download_root).expanduser() if download_root is not None else None
        )
        self.max_hash_bytes = _MAX_HASH_BYTES
        self._entries: dict[str, LibraryEntry] = {}
        if self.path is not None:
            self.load()

    # -- persistence ------------------------------------------------------

    def load(self) -> list[LibraryEntry]:
        """Load entries, treating any malformed file as an empty store."""
        self._entries = {}
        if self.path is None:
            return []
        data = read_json(self.path, None)
        if not isinstance(data, dict):
            return []
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            return []
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                continue
            identifier = raw.get("identifier")
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            entry = LibraryEntry.from_dict(raw)
            if not entry.identifier:
                continue
            self._entries[entry.identifier] = entry
        return self.entries()

    def save(self) -> bool:
        """Atomically write the index; in-memory stores write nothing."""
        if self.path is None:
            return False
        document = {
            "version": SCHEMA_VERSION,
            "entries": [entry.to_dict() for entry in self._entries.values()],
        }
        atomic_write_json(self.path, document)
        return True

    # -- entry access -----------------------------------------------------

    def entries(self) -> list[LibraryEntry]:
        return list(self._entries.values())

    def get(self, identifier: str) -> LibraryEntry | None:
        return self._entries.get(identifier)

    def upsert(self, entry: LibraryEntry) -> LibraryEntry:
        if not isinstance(entry, LibraryEntry):
            raise TypeError("upsert expects a LibraryEntry")
        now = time.time()
        if not entry.added_at:
            entry.added_at = now
        if not entry.last_seen:
            entry.last_seen = now
        if entry.status not in STATUSES:
            entry.status = DEFAULT_STATUS
        self._entries[entry.identifier] = entry
        return entry

    def remove(self, identifier: str) -> bool:
        """Forget metadata only; downloaded files are never touched."""
        return self._entries.pop(identifier, None) is not None

    def set_favorite(self, identifier: str, favorite: bool) -> bool:
        entry = self._entries.get(identifier)
        if entry is None:
            return False
        entry.favorite = bool(favorite)
        return True

    def toggle_favorite(self, identifier: str) -> bool | None:
        entry = self._entries.get(identifier)
        if entry is None:
            return None
        entry.favorite = not entry.favorite
        return entry.favorite

    # -- metadata and magnet links -----------------------------------------

    def _entry_or_raise(self, identifier: str) -> LibraryEntry:
        entry = self._entries.get(identifier)
        if entry is None:
            raise KeyError(identifier)
        return entry

    def set_user_metadata(
        self, identifier: str, values: Mapping[str, Any]
    ) -> LibraryEntry:
        """Apply hand-typed metadata; those fields are then protected.

        Raises :class:`KeyError` for an unknown entry and
        :class:`lgl.metadata.MetadataError` for an invalid value.
        """
        entry = self._entry_or_raise(identifier)
        meta.apply_user_edits(entry, values)
        return entry

    def merge_metadata(
        self,
        identifier: str,
        incoming: meta.GameMetadata,
        *,
        overwrite_user: bool = False,
    ) -> meta.MergeReport:
        """Merge provider metadata without clobbering user-edited fields."""
        entry = self._entry_or_raise(identifier)
        return meta.merge_into(entry, incoming, overwrite_user=overwrite_user)

    def set_magnet(self, identifier: str, uri: str) -> LibraryEntry:
        """Store a validated, extracted magnet URI.

        Raises :class:`lgl.magnet.MagnetError` when the text is not a usable
        magnet URI; nothing is stored in that case.
        """
        entry = self._entry_or_raise(identifier)
        text = uri if isinstance(uri, str) else ""
        if not text.strip():
            self.clear_magnet(identifier)
            return entry
        link = magnetlib.parse_magnet(text)
        entry.magnet = link.uri
        entry.infohash = link.infohash
        entry.magnet_origin = link.origin
        return entry

    def set_infohash(self, identifier: str, infohash: str) -> LibraryEntry:
        """Build and store a magnet URI from a bare, valid infohash."""
        entry = self._entry_or_raise(identifier)
        link = magnetlib.build_magnet(infohash, display_name=entry.title)
        entry.magnet = link.uri
        entry.infohash = link.infohash
        entry.magnet_origin = link.origin
        return entry

    def clear_magnet(self, identifier: str) -> bool:
        entry = self._entries.get(identifier)
        if entry is None or not (entry.magnet or entry.infohash):
            return False
        entry.magnet = ""
        entry.infohash = ""
        entry.magnet_origin = ""
        return True

    def magnet_link(self, identifier: str) -> magnetlib.MagnetLink | None:
        entry = self._entries.get(identifier)
        if entry is None:
            return None
        return magnetlib.magnet_from_entry(
            entry.magnet, entry.infohash, entry.magnet_origin
        )

    def mark_played(self, identifier: str, when: float | None = None) -> bool:
        entry = self._entries.get(identifier)
        if entry is None:
            return False
        entry.last_played = float(when if when is not None else time.time())
        return True

    def genres(self) -> list[str]:
        """Every distinct genre in the library, case-insensitively sorted."""
        seen: dict[str, str] = {}
        for entry in self._entries.values():
            for genre in entry.genres:
                seen.setdefault(genre.casefold(), genre)
        return [seen[key] for key in sorted(seen)]

    # -- scanning ---------------------------------------------------------

    def scan(
        self,
        download_root: Path | None = None,
        *,
        scan_folders: bool = True,
    ) -> ScanReport:
        report = ScanReport()
        if not scan_folders:
            return report

        root = Path(download_root).expanduser() if download_root is not None else self.download_root
        if root is None:
            report.errors.append("No download root configured.")
            return report

        try:
            root = root.resolve()
        except OSError:
            pass

        if not root.is_dir():
            report.errors.append(f"Download root is not a directory: {root}")
            self._mark_missing(set(), report)
            return report

        try:
            children = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            report.errors.append(f"Could not list {root}: {exc}")
            return report

        found: set[str] = set()
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                manifest = read_json(child / MANIFEST_NAME, None)
                has_manifest = isinstance(manifest, dict)
                if not has_manifest and not _has_visible_file(child):
                    continue

                report.scanned += 1
                folder_files = scan_game_folder(child)
                manifest_data: Mapping[str, Any] = manifest if has_manifest else {}

                identifier = _as_str(manifest_data.get("identifier")).strip()
                if not identifier:
                    identifier = child.name
                title = _as_str(manifest_data.get("title")).strip() or child.name
                creator = _as_str(manifest_data.get("creator"))
                year = _manifest_year(manifest_data)
                source_url = (
                    _as_str(manifest_data.get("source_url"))
                    or _as_str(manifest_data.get("download_url"))
                )

                size = sum(int(getattr(f, "size", 0) or 0) for f in folder_files.files)
                file_count = len(folder_files.files)

                if _has_part_file(child):
                    status = "incomplete"
                elif has_manifest and file_count == 0:
                    status = "incomplete"
                elif has_manifest and folder_files.executables:
                    status = "installed"
                else:
                    status = "downloaded"

                compatibility = _compat_status(child)
                cover_path = _find_cover(child, folder_files.files)
                is_game = _looks_like_game(child, folder_files.files, has_manifest)

                existing = self._entries.get(identifier)
                now = time.time()
                entry = LibraryEntry(
                    identifier=identifier,
                    title=title,
                    creator=creator,
                    year=year,
                    folder=str(child),
                    size=size,
                    file_count=file_count,
                    favorite=existing.favorite if existing else False,
                    status=status,
                    compatibility=compatibility,
                    integrity=existing.integrity if existing else "",
                    cover_path=cover_path,
                    added_at=(existing.added_at if existing and existing.added_at else now),
                    last_seen=now,
                    source_url=source_url,
                    notes=existing.notes if existing else "",
                    is_game=is_game,
                )
                if existing is not None:
                    _preserve_extras(entry, existing)
                self._entries[identifier] = entry
                found.add(identifier)
                if existing is None:
                    report.added += 1
                else:
                    report.updated += 1
            except Exception as exc:  # one bad folder must not abort the scan
                report.errors.append(f"{child.name}: {exc}")

        self._mark_missing(found, report)
        if self.path is not None:
            try:
                self.save()
            except OSError as exc:
                report.errors.append(f"Could not save library index: {exc}")
        return report

    def forget_missing(self) -> int:
        """Drop entries whose folder no longer exists. Files are untouched."""
        removed = 0
        for identifier, entry in list(self._entries.items()):
            if entry.folder and Path(entry.folder).is_dir():
                continue
            if self._entries.pop(identifier, None) is not None:
                removed += 1
        return removed

    def _mark_missing(self, found: set[str], report: ScanReport) -> None:
        """Mark vanished folders missing without dropping their metadata."""
        for identifier, entry in list(self._entries.items()):
            if identifier in found:
                continue
            if entry.folder and Path(entry.folder).is_dir():
                # Outside the scanned root (or otherwise not discovered).
                continue
            if entry.status != "missing":
                entry.status = "missing"
                report.missing += 1

    # -- duplicates -------------------------------------------------------

    def _entry_files(self) -> list[tuple[str, Path, int]]:
        """Return (folder_key, path, size) for readable files in known folders."""
        seen_folders: set[str] = set()
        result: list[tuple[str, Path, int]] = []
        for entry in self._entries.values():
            if not entry.folder:
                continue
            folder = Path(entry.folder)
            try:
                key = str(folder.resolve())
            except OSError:
                continue
            if key in seen_folders or not folder.is_dir():
                continue
            seen_folders.add(key)
            try:
                info = scan_game_folder(folder)
            except Exception:
                continue
            for local in info.files:
                path = Path(getattr(local, "path", ""))
                if not path.is_file() or path.is_symlink():
                    continue
                result.append((key, path, int(getattr(local, "size", 0) or 0)))
        return result

    def find_duplicates(self, *, max_hash_bytes: int | None = None) -> list[DuplicateGroup]:
        """Find probable duplicates without deleting or modifying anything.

        Two heuristics are combined: equal basename + size, and equal size +
        SHA-256.  Hashing is limited to files of at most ``max_hash_bytes``
        (default :attr:`max_hash_bytes`) and to sizes that occur in more than
        one folder, so the work stays bounded.
        """
        limit = self.max_hash_bytes if max_hash_bytes is None else int(max_hash_bytes)
        files = self._entry_files()
        groups: list[DuplicateGroup] = []
        reported: set[frozenset[str]] = set()

        # 1. Same basename and same size in different folders.
        by_name_size: dict[tuple[str, int], list[tuple[str, Path, int]]] = {}
        for folder_key, path, size in files:
            if size <= 0:
                continue
            by_name_size.setdefault((path.name.casefold(), size), []).append(
                (folder_key, path, size)
            )
        for (name_key, size), items in sorted(by_name_size.items()):
            folders = {folder for folder, _, _ in items}
            if len(items) < 2 or len(folders) < 2:
                continue
            paths = sorted({str(path) for _, path, _ in items})
            signature = frozenset(paths)
            if signature in reported:
                continue
            reported.add(signature)
            groups.append(
                DuplicateGroup(
                    key=f"{name_key}|{size}",
                    kind="name-size",
                    paths=paths,
                    size=size,
                )
            )

        # 2. Same size and same SHA-256, for size candidates only.
        by_size: dict[int, list[tuple[str, Path, int]]] = {}
        for folder_key, path, size in files:
            if size <= 0 or size > limit:
                continue
            by_size.setdefault(size, []).append((folder_key, path, size))
        hashed: set[str] = set()
        for size, items in sorted(by_size.items()):
            folders = {folder for folder, _, _ in items}
            if len(items) < 2 or len(folders) < 2:
                continue
            by_digest: dict[str, list[tuple[str, Path]]] = {}
            for folder, path, _ in items:
                key = str(path)
                if key in hashed:
                    continue
                hashed.add(key)
                digest = _sha256(path)
                if digest is None:
                    continue
                by_digest.setdefault(digest, []).append((folder, path))
            for digest, matches in sorted(by_digest.items()):
                digest_folders = {folder for folder, _ in matches}
                if len(matches) < 2 or len(digest_folders) < 2:
                    continue
                paths = sorted({str(path) for _, path in matches})
                signature = frozenset(paths)
                if signature in reported:
                    continue
                reported.add(signature)
                groups.append(
                    DuplicateGroup(
                        key=digest,
                        kind="sha256",
                        paths=paths,
                        size=size,
                    )
                )

        return groups

    # -- statistics -------------------------------------------------------

    def disk_usage(self) -> dict[str, Any]:
        entries = [e for e in self._entries.values() if e.status != "missing"]
        total_bytes = sum(max(0, int(e.size)) for e in entries)
        free_bytes = 0
        if self.download_root is not None:
            try:
                free_bytes = int(shutil.disk_usage(self.download_root).free)
            except OSError:
                free_bytes = 0
        largest = sorted(
            ((e.identifier, max(0, int(e.size))) for e in entries),
            key=lambda item: (-item[1], item[0].casefold()),
        )[:5]
        return {
            "total_bytes": total_bytes,
            "game_count": len(entries),
            "free_bytes": free_bytes,
            "largest": largest,
        }

    # -- export / import --------------------------------------------------

    def export_json(self, path: Path) -> int:
        """Write a portable snapshot; the source index is left untouched."""
        document_entries: list[dict[str, Any]] = []
        root = self.download_root
        for entry in self._entries.values():
            data = entry.to_dict()
            data["folder_relative"] = ""
            if root is not None and entry.folder:
                folder = Path(entry.folder)
                if _is_within(folder, root):
                    try:
                        data["folder_relative"] = (
                            folder.resolve().relative_to(root.resolve()).as_posix()
                        )
                    except (OSError, ValueError):
                        data["folder_relative"] = ""
            document_entries.append(data)
        document = {
            "version": SCHEMA_VERSION,
            "exported_at": time.time(),
            "entries": document_entries,
        }
        atomic_write_json(Path(path), document)
        return len(document_entries)

    def import_json(self, path: Path, *, merge: bool = True) -> ImportReport:
        """Validate and load an exported document without touching files."""
        report = ImportReport()
        data = read_json(Path(path), None)
        if not isinstance(data, dict):
            report.errors.append("Import file is not a JSON object.")
            return report
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            report.errors.append("Import file has no entries list.")
            return report

        if not merge:
            self._entries = {}

        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, Mapping):
                report.skipped += 1
                report.errors.append(f"Entry {index} is not an object.")
                continue
            identifier = raw.get("identifier")
            if not isinstance(identifier, str) or not identifier.strip():
                report.skipped += 1
                report.errors.append(
                    f"Entry {index} has a non-string or empty identifier."
                )
                continue
            try:
                entry = LibraryEntry.from_dict(raw)
            except Exception as exc:  # defensive; from_dict is already tolerant
                report.skipped += 1
                report.errors.append(f"Entry {index} could not be read: {exc}")
                continue
            if not entry.identifier:
                report.skipped += 1
                report.errors.append(f"Entry {index} has an empty identifier.")
                continue

            existing = self._entries.get(entry.identifier) if merge else None
            if existing is not None:
                # The current store's favourites always win during a merge.
                entry.favorite = existing.favorite
                entry.integrity = entry.integrity or existing.integrity
                entry.notes = entry.notes or existing.notes
                entry.added_at = existing.added_at or entry.added_at

            if entry.folder and not Path(entry.folder).is_dir():
                entry.status = "missing"

            self._entries[entry.identifier] = entry
            report.imported += 1

        if self.path is not None:
            try:
                self.save()
            except OSError as exc:
                report.errors.append(f"Could not save library index: {exc}")
        return report

    # -- view helpers -----------------------------------------------------

    @staticmethod
    def group_copies(
        entries: Sequence[LibraryEntry],
    ) -> tuple[list[LibraryEntry], dict[str, list[LibraryEntry]]]:
        """Collapse entries that are two copies of the same title.

        Returns the visible entries (one per title, in first-seen order) and a
        mapping from the visible identifier to the other copies of that title.
        Nothing is deleted; the hidden copies stay in the index.
        """
        groups: dict[str, list[LibraryEntry]] = {}
        order: list[str] = []
        for entry in entries:
            key = _title_key(entry)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(entry)

        visible: list[LibraryEntry] = []
        copies: dict[str, list[LibraryEntry]] = {}
        for key in order:
            group = groups[key]
            if len(group) == 1:
                visible.append(group[0])
                continue
            primary = max(group, key=_copy_rank)
            visible.append(primary)
            copies[primary.identifier] = [e for e in group if e is not primary]
        return visible, copies

    @staticmethod
    def sort_entries(
        entries: Sequence[LibraryEntry],
        key: str,
        descending: bool | None = None,
    ) -> list[LibraryEntry]:
        """Order entries for the library view.

        ``key`` is one of :data:`SORT_DEFAULT_DESCENDING`; an unknown key means
        ``title``.  ``descending=None`` keeps each mode's historical default,
        so callers that know nothing about directions (the Tkinter frontend)
        get exactly the order they always did.

        Tie-breakers (title, then identifier) are always ascending and are
        never reversed with the primary key, so toggling direction does not
        reshuffle entries that compare equal.  Entries without the sorted
        value — no title, no year, no size, never seen — go last in both
        directions.
        """
        items = list(entries)
        key = (key or "").casefold()
        if key not in SORT_DEFAULT_DESCENDING:
            key = "title"
        reverse = default_descending(key) if descending is None else bool(descending)

        # 1. Tie-breakers first, ascending.  Python's sort is stable in both
        #    directions, so equal primary values keep this order later.
        items.sort(key=lambda e: ((e.title or "").casefold(), e.identifier))

        # 2. Primary value.
        if key == "title":
            items.sort(
                key=lambda e: str(e.title or "").strip().casefold(), reverse=reverse
            )
        elif key == "size":
            items.sort(key=lambda e: _as_int(e.size), reverse=reverse)
        elif key == "year":
            items.sort(key=lambda e: _year_int(e.year) or 0, reverse=reverse)
        elif key == "added":
            items.sort(key=lambda e: _as_float(e.added_at), reverse=reverse)
        elif key == "played":
            items.sort(key=lambda e: _as_float(e.last_played), reverse=reverse)
        elif key == "developer":
            items.sort(key=lambda e: _developer_key(e), reverse=reverse)
        elif key == "genre":
            items.sort(key=lambda e: _genre_key(e), reverse=reverse)
        elif key == "platform":
            items.sort(key=lambda e: _platform_key(e), reverse=reverse)
        else:  # "recent" — last seen
            items.sort(key=lambda e: _as_float(e.last_seen), reverse=reverse)

        # 3. Missing values last, then keep the rest as sorted.
        items.sort(key=lambda e: _sort_missing(e, key))
        return items

    @staticmethod
    def filter_entries(
        entries: Sequence[LibraryEntry],
        *,
        query: str = "",
        status: str | None = None,
        favorite: bool | None = None,
        compatibility: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        genre: str | None = None,
        games_only: bool = False,
        include_missing: bool = True,
        exclude_adult: bool = False,
    ) -> list[LibraryEntry]:
        needle = (query or "").casefold().strip()
        results: list[LibraryEntry] = []
        for entry in entries:
            if needle:
                haystack = " ".join(
                    (entry.title, entry.identifier, entry.creator)
                ).casefold()
                if needle not in haystack:
                    continue
            if games_only and not entry.is_game:
                continue
            if exclude_adult and looks_adult(entry):
                continue
            if not include_missing and entry.status == "missing":
                continue
            if status is not None and entry.status != status:
                continue
            if favorite is not None and bool(entry.favorite) != bool(favorite):
                continue
            if compatibility is not None and entry.compatibility != compatibility:
                continue
            if genre is not None:
                wanted = genre.casefold()
                if not any(item.casefold() == wanted for item in entry.genres):
                    continue
            if year_from is not None or year_to is not None:
                year = _year_int(entry.year)
                if year is None:
                    continue
                if year_from is not None and year < int(year_from):
                    continue
                if year_to is not None and year > int(year_to):
                    continue
            results.append(entry)
        return results


__all__ = [
    "LibraryEntry",
    "LibraryStore",
    "SORT_DEFAULT_DESCENDING",
    "ADULT_TAGS",
    "default_descending",
    "looks_adult",
    "ScanReport",
    "DuplicateGroup",
    "ImportReport",
    "STATUSES",
    "SCHEMA_VERSION",
]
