"""File classification for archive listings and downloaded folders.

Internet Archive's ``format`` field is not reliable for game payloads (for
example self-extracting ``.sh`` installers are sometimes labelled
``MPEG Audio``).  Extension + magic bytes take precedence here.
"""

from __future__ import annotations

from pathlib import Path

SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".run")
ARCHIVE_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2",
    ".tar.zst", ".zip", ".7z", ".rar", ".gz", ".xz", ".bz2", ".zst",
)
DOCUMENT_SUFFIXES = (".pdf", ".txt", ".md", ".nfo", ".html", ".htm", ".epub", ".rtf", ".chm")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp")
DISC_SUFFIXES = (".iso", ".img", ".bin", ".cue")

AUX_FORMATS = {"metadata", "item tile", "archive bittorrent", "jpeg thumb", "item image"}
AUX_NAME_PREFIXES = ("__ia_thumb", "logo_", "screenshot-")
AUX_NAMES = {"_meta.xml", "_files.xml", "_meta.sqlite", "_reviews.xml", "_meta.sqlite"}


def _lower_name(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def is_auxiliary(name: str, format_hint: str | None = None) -> bool:
    """True for archive website chrome, screenshots, torrents, and metadata."""
    lower = name.replace("\\", "/").lower()
    base = _lower_name(lower)
    fmt = (format_hint or "").strip().lower()
    if fmt in AUX_FORMATS:
        return True
    if lower.startswith("images/") or "/images/" in lower:
        return True
    if base.startswith(AUX_NAME_PREFIXES):
        return True
    if base in AUX_NAMES or base.endswith("_meta.sqlite"):
        return True
    if base.endswith("_archive.torrent"):
        return True
    return False


def classify_file(name: str, format_hint: str | None = None) -> str:
    """Human-readable kind used in the UI and for launch decisions."""
    lower = _lower_name(name)
    if lower.endswith(SHELL_SUFFIXES):
        return "Shell installer"
    if lower.endswith(".appimage"):
        return "AppImage"
    if lower.endswith(ARCHIVE_SUFFIXES):
        return "Archive"
    if lower.endswith(DOCUMENT_SUFFIXES):
        return "Document"
    if lower.endswith(IMAGE_SUFFIXES):
        return "Image"
    if lower.endswith(DISC_SUFFIXES):
        return "Disc image"
    if lower.endswith(".desktop"):
        return "Desktop entry"
    if lower.endswith((".exe", ".msi")):
        return "Windows executable"
    if lower.endswith(".deb"):
        return "Debian package"
    if lower.endswith(".rpm"):
        return "RPM package"
    fmt = (format_hint or "").strip()
    if fmt and fmt.lower() not in ("unknown", "metadata"):
        return fmt
    return "File"


def is_document(name: str) -> bool:
    return classify_file(name) == "Document"


def is_shell(name: str) -> bool:
    return _lower_name(name).endswith(SHELL_SUFFIXES)


def is_appimage(name: str) -> bool:
    return _lower_name(name).endswith(".appimage")


def looks_like_elf(path: Path | str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def looks_like_shell(path: Path | str) -> bool:
    """Detect a text script by shebang, without executing it."""
    try:
        with open(path, "rb") as fh:
            first = fh.readline(256)
        return first.startswith(b"#!") and b"sh" in first
    except OSError:
        return False


def is_launchable_kind(name: str) -> bool:
    return is_shell(name) or is_appimage(name) or _lower_name(name).endswith(".run")
