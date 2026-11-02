"""Small helpers shared by the archive, downloader, library, and UI layers."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class AppError(Exception):
    """Base error with a message suitable for showing to the user."""


class ArchiveError(AppError):
    """Archive.org metadata could not be retrieved or understood."""


class DownloadError(AppError):
    """A download could not be started or completed."""


class LaunchError(AppError):
    """A downloaded file refused to be launched safely."""


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def human_size(num: int | float | None) -> str:
    if num is None:
        return "—"
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "—"


_SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1024, "kb": 1024, "kib": 1024,
    "m": 1024 ** 2, "mb": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1024 ** 3, "gb": 1024 ** 3, "gib": 1024 ** 3,
    "t": 1024 ** 4, "tb": 1024 ** 4, "tib": 1024 ** 4,
}
_SIZE_TEXT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([A-Za-z]{0,3})")


def parse_size(value: Any) -> int | None:
    """Turn a size a source reported into bytes, or ``None`` when it is not one.

    Accepts a byte count (already a number) or text such as ``"7.69 GB"``.
    Every unit spelling is read as binary because :func:`human_size` prints
    binary units, so the two functions round-trip.  Anything else - a word, a
    range, an unknown unit - is refused rather than guessed at.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > 40:
        return None
    match = _SIZE_TEXT.fullmatch(text)
    if match is None:
        return None
    number, unit = match.group(1), match.group(2).casefold()
    if "," in number:
        whole, _, fraction = number.partition(",")
        # "1,024 MB" uses a thousands separator; "7,7 GB" a decimal comma.
        number = whole + fraction if len(fraction) == 3 else f"{whole}.{fraction}"
    factor = _SIZE_UNITS.get(unit)
    if factor is None:
        return None
    try:
        size = int(round(float(number) * factor))
    except (TypeError, ValueError, OverflowError):
        return None
    return size if size > 0 else None


def human_speed(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return "—"
    return f"{human_size(bytes_per_second)}/s"


def human_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    if seconds < 1:
        return "few seconds"
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


# --------------------------------------------------------------------------
# HTML description cleanup (never feed archive HTML to the UI as markup)
# --------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    _BLOCK = {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4",
        "h5", "h6", "tr", "table", "blockquote", "pre", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        tag = tag.lower()
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if not self._skip:
            self.parts.append(data)


def strip_html(value: Any) -> str:
    """Convert an archive.org description to plain, readable text."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "\n\n".join(str(v) for v in value)
    text = html.unescape(str(value))
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*]')
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_identifier(identifier: str) -> str:
    """Archive.org identifiers are simple; reject anything path-like."""
    if not isinstance(identifier, str) or not _IDENTIFIER.match(identifier):
        raise ValueError(f"Invalid archive.org identifier: {identifier!r}")
    return identifier


def safe_filename(name: str, fallback: str = "download") -> str:
    """Return a single safe path component, portable to Windows and POSIX.

    Windows-invalid characters and reserved device names are neutralized even
    on Linux so a downloaded library can be copied to Windows without surprises.
    """
    if not isinstance(name, str):
        raise ValueError("Filename must be a string")
    name = unicodedata.normalize("NFC", name)
    name = name.replace("/", "_").replace("\\", "_")
    name = _WINDOWS_INVALID.sub("_", name)
    name = _CONTROL.sub("", name)
    name = name.strip().rstrip(". ")
    if not name:
        name = fallback
    stem, dot, ext = name.rpartition(".")
    check = (stem if dot else name).upper()
    if check in _WINDOWS_RESERVED:
        name = "_" + name
    # Keep a useful extension when truncating extremely long names.
    if len(name.encode("utf-8")) > 240:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 16:
            name = stem[: 200] + "." + ext
        else:
            name = name[: 200]
    if name in (".", ".."):
        name = fallback
    return name


def safe_relpath(name: str) -> str:
    """Sanitize an archive path while preserving safe subdirectories."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Empty file name")
    parts: list[str] = []
    for part in name.replace("\\", "/").split("/"):
        part = part.strip()
        if not part or part in (".", ".."):
            continue
        parts.append(safe_filename(part))
    if not parts:
        raise ValueError(f"Unsafe file name: {name!r}")
    return os.path.join(*parts)


def is_within(path: Path | str, root: Path | str) -> bool:
    """True if *path* is *root* or located beneath it (after resolving)."""
    try:
        p = Path(path).resolve()
        r = Path(root).resolve()
        return p == r or r in p.parents
    except (OSError, ValueError):
        return False


def ensure_directory(path: Path | str) -> Path:
    p = Path(path).expanduser().resolve()
    if p.exists() and not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    p.mkdir(parents=True, exist_ok=True)
    if not os.access(p, os.W_OK | os.X_OK):
        raise PermissionError(f"Directory is not writable: {p}")
    return p


def check_download_dir(path: Path | str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise ValueError(f"Download directory does not exist: {p}")
    if not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    if not os.access(p, os.W_OK | os.X_OK):
        raise PermissionError(f"Download directory is not writable: {p}")
    return p


# --------------------------------------------------------------------------
# XDG locations, JSON cache
# --------------------------------------------------------------------------

def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "lumora"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return downloads
    return Path.home()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def checksum_short(value: str | None, length: int = 10) -> str:
    if not value:
        return "—"
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def now() -> float:
    return time.time()
