"""Best-effort game compatibility detection.

This module inspects downloaded files without executing them and without
extracting archives to disk.  Every result is a *guess*: file names and magic
bytes cannot prove that a game will run.  The public API deliberately reports
confidence, reasons, and suggested runtimes so the UI can avoid overclaiming.

Standard library only.  Archives are inspected with :mod:`zipfile` and
:mod:`tarfile`, reading member headers (and at most a few bytes of a member's
payload) in memory.  Nothing is written anywhere by this module.
"""

from __future__ import annotations

import os
import zipfile
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .filetypes import is_appimage, is_document, is_shell

NATIVE = "Native Linux"
WINDOWS = "Windows"
DOS = "DOS"
JAVA = "Java"
SOURCE_ONLY = "Source only"
UNKNOWN = "Unknown"

STATUSES = (NATIVE, WINDOWS, DOS, JAVA, SOURCE_ONLY, UNKNOWN)
CONFIDENCES = ("high", "medium", "low")

# Highest priority wins when several platforms are present.
_PRIORITY = {
    NATIVE: 5,
    WINDOWS: 4,
    DOS: 3,
    JAVA: 2,
    SOURCE_ONLY: 1,
    UNKNOWN: 0,
}
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

_MAX_ARCHIVE_ENTRIES = 2000
_MAX_SNIFF_MEMBERS = 200
_MAX_ARCHIVE_BYTES_READ = 4

SHELL_REASON = (
    "Shell installer; the target platform cannot be determined without reviewing it."
)

_RUNTIME_WINDOWS = "Wine or Proton (not bundled)"
_RUNTIME_DOS = "DOSBox (not bundled)"
_RUNTIME_JAVA = "Java runtime (not bundled)"
_RUNTIME_BUILD = "Build toolchain (not bundled)"

_PLATFORM_HINT = {
    NATIVE: "native Linux binaries were also found.",
    WINDOWS: "those files would need Wine or Proton.",
    DOS: "those files would need DOSBox.",
    JAVA: "those files would need a Java runtime.",
    SOURCE_ONLY: "source archives would need to be built first.",
}

# Suffixes that indicate buildable source code inside an archive.
_SOURCE_SUFFIXES = frozenset(
    {
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx", ".hh",
        ".rs", ".go", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
        ".java", ".cs", ".lua", ".gd", ".rb", ".php", ".pl", ".pas",
        ".s", ".asm", ".glsl", ".vert", ".frag", ".hlsl", ".shader",
        ".uproject",
    }
)
_SOURCE_NAMES = frozenset(
    {
        "makefile", "gnumakefile", "cmakelists.txt", "meson.build",
        "cargo.toml", "package.json", "project.godot", "configure",
        "configure.ac", "configure.in", "sconstruct", "build.gradle",
        "settings.gradle", "pom.xml", "go.mod", "pyproject.toml",
        "setup.py", "makefile.am", "makefile.in",
    }
)

# Compound suffixes must be checked before their shorter parents.
_TAR_SUFFIXES = (
    ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar",
)
_ZIP_SUFFIXES = (".zip",)
# Cannot be inspected with the Python standard library.
_UNINSPECTABLE_ARCHIVES = (".7z", ".rar", ".tar.zst", ".tzst", ".zst", ".lz", ".lzma")
_DISC_SUFFIXES = (".iso", ".img", ".cue", ".bin", ".mdf", ".nrg")

_DOS_TEXT_MARKERS = (
    b"dosbox",
    b"command.com",
    b"autoexec",
    b"config.sys",
    b"c:\\",
    b"cd \\",
    b"@echo off",
)


@dataclass(frozen=True)
class Compatibility:
    """A deliberately uncertain compatibility summary.

    ``platforms`` contains the status strings that were actually detected
    (never ``Unknown``), even when a higher-priority status wins.
    """

    status: str
    confidence: str
    reasons: tuple[str, ...] = ()
    runtimes: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    platforms: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Unsupported compatibility status: {self.status!r}")
        if self.confidence not in CONFIDENCES:
            raise ValueError(
                f"Unsupported compatibility confidence: {self.confidence!r}"
            )
        # Accept lists/sets for convenience while keeping the object immutable.
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "runtimes", tuple(str(item) for item in self.runtimes))
        object.__setattr__(self, "files", tuple(str(item) for item in self.files))
        object.__setattr__(self, "platforms", frozenset(self.platforms))


@dataclass(frozen=True)
class _Detection:
    status: str
    confidence: str
    reason: str
    runtimes: tuple[str, ...]
    file: str = ""


@dataclass
class _ArchiveScan:
    source: bool = False
    elf: bool = False
    pe: bool = False
    java: bool = False
    dos: bool = False
    entries: int = 0
    truncated: bool = False
    inspected: bool = True
    source_examples: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_paths(paths: Iterable[Path]) -> Compatibility:
    """Analyze explicit files (or paths) and return a compatibility guess.

    Directories are not expanded here; use :func:`analyze_folder` for that.
    Nothing is executed and nothing is extracted.
    """
    raw_paths = [Path(p).expanduser() for p in paths]
    if not raw_paths:
        return Compatibility(
            status=UNKNOWN,
            confidence="low",
            reasons=("No files were provided, so the platform cannot be determined.",),
        )

    detections: list[_Detection] = []
    files: list[str] = []
    for path in raw_paths:
        files.append(str(path))
        detections.append(_detect_file(path))

    detected_platforms = {d.status for d in detections if d.status != UNKNOWN}
    if detected_platforms:
        status = max(detected_platforms, key=lambda value: _PRIORITY[value])
        relevant = [d for d in detections if d.status == status]
        confidence = max(
            (d.confidence for d in relevant), key=lambda value: _CONFIDENCE_RANK[value]
        )
    else:
        status = UNKNOWN
        confidence = max(
            (d.confidence for d in detections), key=lambda value: _CONFIDENCE_RANK[value]
        )

    reasons: list[str] = []

    def add_reason(text: str) -> None:
        if text and text not in reasons:
            reasons.append(text)

    # Reasons for the winning platform first, then every other detected one.
    ordered = sorted(
        detections, key=lambda d: (-_PRIORITY[d.status], d.reason, d.file)
    )
    for detection in ordered:
        add_reason(detection.reason)

    for platform in sorted(
        detected_platforms, key=lambda value: _PRIORITY[value], reverse=True
    ):
        if platform == status:
            continue
        names = [d.file for d in detections if d.status == platform]
        sample = _sample_names(names)
        add_reason(
            f"Also present: {platform} ({len(names)} file(s){sample}); "
            + _PLATFORM_HINT[platform]
        )

    add_reason(
        "Best-effort detection only; no file was executed and compatibility "
        "is not guaranteed."
    )

    runtimes: list[str] = []
    for platform in sorted(
        detected_platforms, key=lambda value: _PRIORITY[value], reverse=True
    ):
        for detection in detections:
            if detection.status != platform:
                continue
            for runtime in detection.runtimes:
                if runtime not in runtimes:
                    runtimes.append(runtime)

    return Compatibility(
        status=status,
        confidence=confidence,
        reasons=tuple(reasons[:24]),
        runtimes=tuple(runtimes),
        files=tuple(sorted(files)),
        platforms=frozenset(detected_platforms),
    )


def analyze_folder(folder: Path | str) -> Compatibility:
    """Analyze all regular files in ``folder`` recursively (no extraction)."""
    root = Path(folder).expanduser()
    try:
        is_dir = root.is_dir()
    except OSError:
        is_dir = False
    if not is_dir:
        return Compatibility(
            status=UNKNOWN,
            confidence="low",
            reasons=(
                f"Folder does not exist or is not a directory: {root}",
                "No game files could be inspected.",
            ),
            files=(),
        )

    found: list[Path] = []

    def _ignore_oserror(_exc: OSError) -> None:
        return None

    try:
        for current, dirs, names in os.walk(
            root, followlinks=False, onerror=_ignore_oserror
        ):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and not Path(current, d).is_symlink()
            )
            for name in sorted(names):
                if name.startswith(".") or name.endswith(".part"):
                    continue
                path = Path(current) / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                except OSError:
                    continue
                found.append(path)
                if len(found) >= _MAX_ARCHIVE_ENTRIES:
                    break
            if len(found) >= _MAX_ARCHIVE_ENTRIES:
                break
    except OSError:
        pass

    if not found:
        return Compatibility(
            status=UNKNOWN,
            confidence="low",
            reasons=(
                f"No files were found in {root}, so the platform cannot be determined.",
            ),
            files=(),
        )
    return analyze_paths(found)


def explain(compat: Compatibility) -> str:
    """Return a concise, non-guaranteeing human-readable summary."""
    if compat.status == UNKNOWN:
        lead = (
            "The likely platform could not be determined "
            f"({compat.confidence} confidence)."
        )
    else:
        lead = f"Likely platform: {compat.status} ({compat.confidence} confidence)."

    parts = [
        lead,
        "This is a best-effort guess from file names and headers; no file was "
        "executed, so it is not a guarantee that the game will run.",
    ]
    if compat.reasons:
        parts.append("Why: " + "; ".join(compat.reasons[:6]) + ".")
    if compat.runtimes:
        parts.append("Possibly needed: " + ", ".join(compat.runtimes) + ".")
    if compat.files:
        parts.append(f"Files examined: {len(compat.files)}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Per-file detection
# ---------------------------------------------------------------------------


def _detect_file(path: Path) -> _Detection:
    label = str(path)
    try:
        if not path.is_file():
            if path.is_dir():
                reason = "Directory passed to analyze_paths; use analyze_folder instead."
            else:
                reason = "File is missing or is not a regular file; it could not be inspected."
            return _Detection(UNKNOWN, "low", reason, (), label)
    except OSError:
        return _Detection(
            UNKNOWN, "low", "File could not be accessed; it could not be inspected.", (), label
        )

    name = path.name
    lower = name.lower()

    # Shell installers are deliberately not treated as native.  A self-extracting
    # installer usually contains a payload whose platform is unknown until the
    # script is reviewed.
    if is_shell(name):
        return _Detection(UNKNOWN, "low", SHELL_REASON, (), label)

    if is_appimage(name):
        head = _read_head(path, 4)
        if head is None:
            return _Detection(
                NATIVE,
                "medium",
                "AppImage by file name; the file could not be read, so the format "
                "was not confirmed.",
                (),
                label,
            )
        return _Detection(
            NATIVE,
            "high",
            "AppImage bundle for Linux; AppImages normally contain their own "
            "runtime and do not use the ELF magic at the start of the file.",
            (),
            label,
        )

    if lower.endswith(".msi"):
        return _Detection(
            WINDOWS,
            "high",
            "Windows Installer package; installing it requires Windows or Wine.",
            (_RUNTIME_WINDOWS,),
            label,
        )

    if lower.endswith((".jar", ".class")):
        return _Detection(
            JAVA,
            "high",
            "Java archive/class file; it requires a Java runtime.",
            (_RUNTIME_JAVA,),
            label,
        )

    if lower.endswith(".exe"):
        return _detect_exe(path, label)

    if lower.endswith((".com", ".dos")):
        return _Detection(
            DOS,
            "medium",
            "DOS-era executable by file extension; it likely requires DOSBox. "
            "The format was not validated by reading the file.",
            (_RUNTIME_DOS,),
            label,
        )

    if lower.endswith((".bat", ".cfg")):
        head = _read_head(path, 4096)
        if head is not None and _has_dos_markers(head):
            return _Detection(
                DOS,
                "medium",
                "DOS batch/configuration file (DOS markers such as DOSBox or a "
                "C:\\ path were found); it likely belongs with a DOS game.",
                (_RUNTIME_DOS,),
                label,
            )
        return _Detection(
            UNKNOWN,
            "low",
            "Batch/configuration file without clear DOS markers; the target "
            "platform cannot be determined from it.",
            (),
            label,
        )

    if _is_tar_archive(lower) or lower.endswith(_ZIP_SUFFIXES):
        return _detect_archive(path, lower, label)

    if lower.endswith(_UNINSPECTABLE_ARCHIVES):
        return _Detection(
            UNKNOWN,
            "low",
            "Archive format cannot be inspected with the Python standard library; "
            "the contents were not guessed.",
            (),
            label,
        )

    if is_document(name):
        return _Detection(
            UNKNOWN,
            "low",
            "Documentation/non-executable file; it does not identify a game platform.",
            (),
            label,
        )

    if lower.endswith(_DISC_SUFFIXES):
        return _Detection(
            UNKNOWN,
            "low",
            "Disc image; its contents were not inspected, so the platform is unknown.",
            (),
            label,
        )

    # Fall back to magic bytes for extensionless binaries and unusual names.
    head = _read_head(path, 64)
    if head is None:
        return _Detection(
            UNKNOWN, "low", "File could not be read; the platform is unknown.", (), label
        )
    if head.startswith(b"\x7fELF"):
        return _Detection(
            NATIVE,
            "high",
            "ELF executable, the native Linux binary format.",
            (),
            label,
        )
    if head.startswith(b"MZ"):
        if _pe_signature_ok(path):
            return _Detection(
                WINDOWS,
                "high",
                "Windows PE executable (MZ header with a valid PE signature); it "
                "requires Windows, Wine, or Proton.",
                (_RUNTIME_WINDOWS,),
                label,
            )
        return _Detection(
            DOS,
            "low",
            "MZ executable without a PE header; this is likely DOS (it may also be "
            "a 16-bit Windows program). Try DOSBox and be cautious: neither "
            "platform is confirmed.",
            (_RUNTIME_DOS,),
            label,
        )
    if not head:
        return _Detection(UNKNOWN, "low", "Empty file; the platform is unknown.", (), label)
    if head.startswith(b"#!"):
        return _Detection(
            UNKNOWN,
            "low",
            "Script file; the target platform cannot be determined from the shebang.",
            (),
            label,
        )
    return _Detection(
        UNKNOWN,
        "low",
        "File format not recognised; the platform cannot be determined.",
        (),
        label,
    )


def _detect_exe(path: Path, label: str) -> _Detection:
    head = _read_head(path, 4)
    if head is None:
        return _Detection(
            WINDOWS,
            "low",
            "File has an .exe suffix but could not be read or validated. Windows is "
            "assumed; use Wine with caution.",
            (_RUNTIME_WINDOWS,),
            label,
        )
    if head.startswith(b"\x7fELF"):
        return _Detection(
            NATIVE,
            "high",
            "ELF binary despite the .exe suffix; this is a Linux executable.",
            (),
            label,
        )
    if head.startswith(b"MZ"):
        if _pe_signature_ok(path):
            return _Detection(
                WINDOWS,
                "high",
                "Windows PE executable (MZ header with a valid PE signature); it "
                "requires Windows, Wine, or Proton.",
                (_RUNTIME_WINDOWS,),
                label,
            )
        return _Detection(
            DOS,
            "low",
            "MZ executable without a PE header; this is likely DOS (it may also be "
            "a 16-bit Windows program). Try DOSBox and be cautious: neither "
            "platform is confirmed.",
            (_RUNTIME_DOS,),
            label,
        )
    return _Detection(
        WINDOWS,
        "low",
        "File has an .exe suffix but no MZ header, so the executable format could "
        "not be validated. Windows is assumed; use Wine with caution.",
        (_RUNTIME_WINDOWS,),
        label,
    )


# ---------------------------------------------------------------------------
# Archive inspection (read-only, in memory)
# ---------------------------------------------------------------------------


def _detect_archive(path: Path, lower: str, label: str) -> _Detection:
    try:
        if _is_tar_archive(lower):
            scan = _scan_tar(path)
        else:
            scan = _scan_zip(path)
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        return _Detection(
            UNKNOWN,
            "low",
            f"Archive could not be inspected ({type(exc).__name__}); the platform "
            "is unknown.",
            (),
            label,
        )

    if scan is None:
        return _Detection(
            UNKNOWN,
            "low",
            "Archive could not be opened; the platform is unknown.",
            (),
            label,
        )
    if not scan.inspected:
        return _Detection(
            UNKNOWN,
            "low",
            "Archive has no readable entries; the platform is unknown.",
            (),
            label,
        )

    truncation = " (archive listing truncated)" if scan.truncated else ""
    examples = _sample_names(scan.source_examples, limit=4)

    if scan.elf:
        return _Detection(
            NATIVE,
            "medium",
            "Archive contains what appears to be an ELF binary; it was not "
            f"extracted or executed{truncation}.",
            (),
            label,
        )
    if scan.pe:
        return _Detection(
            WINDOWS,
            "medium",
            "Archive contains a Windows executable/installer; it was not extracted "
            f"or executed{truncation}.",
            (_RUNTIME_WINDOWS,),
            label,
        )
    if scan.java:
        return _Detection(
            JAVA,
            "medium",
            "Archive contains Java .jar/.class files; it was not extracted or "
            f"executed{truncation}.",
            (_RUNTIME_JAVA,),
            label,
        )
    if scan.dos:
        return _Detection(
            DOS,
            "low",
            "Archive contains DOS-style executables (.com/.dos); it was not "
            f"extracted or executed{truncation}.",
            (_RUNTIME_DOS,),
            label,
        )
    if scan.source:
        return _Detection(
            SOURCE_ONLY,
            "medium",
            "Source code archive"
            + (f" (e.g.{examples})" if examples else "")
            + "; no prebuilt Linux, Windows, or Java game binary was found. It "
            f"likely needs to be built from source{truncation}.",
            (_RUNTIME_BUILD,),
            label,
        )
    return _Detection(
        UNKNOWN,
        "low",
        "Archive was inspected but no recognised executables or source indicators "
        f"were found{truncation}.",
        (),
        label,
    )


def _scan_zip(path: Path) -> _ArchiveScan | None:
    if not zipfile.is_zipfile(path):
        return None
    scan = _ArchiveScan()
    with zipfile.ZipFile(path) as archive:
        try:
            infos = archive.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            raise zipfile.BadZipFile(str(exc)) from exc
        if len(infos) > _MAX_ARCHIVE_ENTRIES:
            scan.truncated = True
        sniffed = 0
        for info in infos[:_MAX_ARCHIVE_ENTRIES]:
            scan.entries += 1
            if info.is_dir():
                continue
            name = info.filename
            if not _safe_member_name(name):
                continue
            _note_member(scan, name)
            if sniffed < _MAX_SNIFF_MEMBERS and _should_sniff(name):
                sniffed += 1
                try:
                    with archive.open(info, "r") as member:
                        head = member.read(_MAX_ARCHIVE_BYTES_READ)
                except (OSError, RuntimeError, zipfile.BadZipFile, ValueError):
                    head = b""
                _note_magic(scan, head)
    scan.inspected = scan.entries > 0
    return scan


def _scan_tar(path: Path) -> _ArchiveScan | None:
    scan = _ArchiveScan()
    try:
        archive = tarfile.open(path, mode="r:*")
    except (tarfile.TarError, OSError):
        return None
    with archive:
        sniffed = 0
        try:
            for member in archive:
                scan.entries += 1
                if scan.entries > _MAX_ARCHIVE_ENTRIES:
                    scan.truncated = True
                    break
                if not member.isfile():
                    continue
                name = member.name
                if not _safe_member_name(name):
                    continue
                _note_member(scan, name)
                if sniffed < _MAX_SNIFF_MEMBERS and _should_sniff(name):
                    sniffed += 1
                    try:
                        extracted = archive.extractfile(member)
                        head = extracted.read(_MAX_ARCHIVE_BYTES_READ) if extracted else b""
                        if extracted is not None:
                            extracted.close()
                    except (tarfile.TarError, OSError, ValueError):
                        head = b""
                    _note_magic(scan, head)
        except (tarfile.TarError, OSError, EOFError, ValueError):
            # Keep whatever was learned before the archive became unreadable.
            pass
    scan.inspected = scan.entries > 0
    return scan


def _note_member(scan: _ArchiveScan, name: str) -> None:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    lower = base.lower()
    suffix = Path(lower).suffix
    if lower in _SOURCE_NAMES or suffix in _SOURCE_SUFFIXES:
        scan.source = True
        if len(scan.source_examples) < 20:
            scan.source_examples.append(base)
    if suffix in (".jar", ".class"):
        scan.java = True
    if suffix in (".exe", ".msi"):
        scan.pe = True
    if suffix in (".com", ".dos"):
        scan.dos = True
    if suffix == ".appimage":
        scan.elf = True


def _note_magic(scan: _ArchiveScan, head: bytes) -> None:
    if head.startswith(b"\x7fELF"):
        scan.elf = True
    elif head.startswith(b"MZ"):
        scan.pe = True


def _should_sniff(name: str) -> bool:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not base:
        return False
    suffix = Path(base).suffix
    if suffix in _SOURCE_SUFFIXES or suffix in (".jar", ".class"):
        return False
    # Extensionless binaries are common in source archives; so are clearly
    # executable-ish names.  Only a few bytes are read.
    return not suffix or suffix in (".bin", ".x86", ".x86_64", ".run", ".appimage")


def _safe_member_name(name: str) -> bool:
    if not name or "\x00" in name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if len(normalized) >= 2 and normalized[1] == ":":
        return False
    if any(part == ".." for part in normalized.split("/")):
        return False
    return True


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _read_head(path: Path, size: int) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read(size)
    except OSError:
        return None


def _pe_signature_ok(path: Path) -> bool:
    """Validate the DOS header offset and the PE signature, without loading it all."""
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            raw_offset = handle.read(4)
            if len(raw_offset) < 4:
                return False
            offset = int.from_bytes(raw_offset, "little")
            if offset < 0x40 or offset > 64 * 1024 * 1024:
                return False
            try:
                size = path.stat().st_size
            except OSError:
                return False
            if offset + 4 > size:
                return False
            handle.seek(offset)
            return handle.read(4) == b"PE\x00\x00"
    except OSError:
        return False


def _has_dos_markers(head: bytes) -> bool:
    lowered = head.lower()
    return any(marker in lowered for marker in _DOS_TEXT_MARKERS)


def _is_tar_archive(lower_name: str) -> bool:
    return lower_name.endswith(_TAR_SUFFIXES)


def _sample_names(names: list[str], limit: int = 3) -> str:
    unique: list[str] = []
    for name in names:
        base = Path(name.replace("\\", "/")).name
        if base and base not in unique:
            unique.append(base)
    if not unique:
        return ""
    shown = ", ".join(unique[:limit])
    if len(unique) > limit:
        shown += ", …"
    return f": {shown}"


__all__ = [
    "Compatibility",
    "NATIVE",
    "WINDOWS",
    "DOS",
    "JAVA",
    "SOURCE_ONLY",
    "UNKNOWN",
    "STATUSES",
    "analyze_paths",
    "analyze_folder",
    "explain",
]
