"""Local media folders: scanning, matching and missing-file detection.

Roadmap section 15: scan folders the user chose, match files to metadata with a
manual correction option, support local posters and subtitles, detect moved or
missing files, let folders and patterns be excluded, and never scan unrelated
directories without permission.

The permission guard lives here: a root that is a filesystem root, a home
directory, or a system directory is refused outright rather than quietly
skipped.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".mpg", ".mpeg", ".wmv")
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi")
ARTWORK_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
POSTER_NAMES = ("poster", "folder", "cover", "fanart", "backdrop")

MAX_FILES = 20000
MAX_DEPTH = 6

_REFUSED_ROOTS = {
    "/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/proc", "/sys", "/dev", "/boot", "/var",
    "c:\\", "c:/", "c:\\windows", "c:/windows", "c:\\program files", "c:/program files",
}

_SXXEYY = re.compile(r"(?i)\bS(\d{1,2})[ ._-]?E(\d{1,3})\b")
_EPISODE = re.compile(r"(?i)\b(?:episode|ep|e)[ ._-]?(\d{1,3})\b")
_YEAR = re.compile(r"(?:^|[\s._(\[])((?:19|20)\d{2})(?:[\s._)\]]|$)")
_SEASON = re.compile(r"(?i)\bseason[ ._-]?(\d{1,2})\b")
_JUNK = re.compile(r"(?i)\b(1080p|2160p|720p|480p|bluray|brrip|webrip|web[-.]?dl|hdtv|x264|x265|h264|h265|hevc|aac|ac3|dts|proper|repack|extended|remux|10bit)\b")


@dataclass(frozen=True)
class MediaFile:
    path: Path
    size: int = 0
    title: str = ""
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    subtitles: tuple[Path, ...] = ()
    artwork: Path | None = None

    @property
    def key(self) -> str:
        bits = [self.title.casefold()]
        if self.year:
            bits.append(str(self.year))
        if self.season is not None and self.episode is not None:
            bits.append(f"s{self.season:02d}e{self.episode:03d}")
        return "|".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size": self.size,
            "title": self.title,
            "year": self.year,
            "season": self.season,
            "episode": self.episode,
            "subtitles": [str(item) for item in self.subtitles],
            "artwork": str(self.artwork) if self.artwork else "",
        }


@dataclass
class ScanResult:
    files: list[MediaFile] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.files)


def guard_root(path: Path | str) -> tuple[bool, str]:
    """Refuse to walk a directory that is not a media folder."""
    target = Path(os.path.abspath(str(path)))
    normalised = str(target).replace("\\", "/").rstrip("/").lower() or "/"
    if normalised in _REFUSED_ROOTS:
        return False, "That is a system directory, not a media folder."
    try:
        home = Path.home()
    except (RuntimeError, OSError):  # pragma: no cover - exotic environments
        home = None
    if home is not None and target == home:
        return False, "Pick a folder inside your home directory, not the whole home directory."
    if not target.exists():
        return False, "That folder does not exist."
    if not target.is_dir():
        return False, "That path is a file, not a folder."
    return True, ""


def parse_name(filename: str | Path) -> tuple[str, int | None, int | None, int | None]:
    """Best-effort title/year/season/episode from a release-style filename."""
    stem = Path(str(filename)).stem
    text = _JUNK.sub(" ", stem.replace("_", " ").replace(".", " "))
    season = episode = None
    match = _SXXEYY.search(stem) or _SXXEYY.search(text)
    if match:
        season, episode = int(match.group(1)), int(match.group(2))
        text = text[: match.start()] + " " + text[match.end() :]
    else:
        single = _EPISODE.search(text)
        if single and _SEASON.search(text):
            episode = int(single.group(1))
            text = text[: single.start()] + " " + text[single.end() :]
        season_match = _SEASON.search(text)
        if season_match:
            season = int(season_match.group(1))
            text = text[: season_match.start()] + " " + text[season_match.end() :]
    year = None
    year_match = _YEAR.search(text)
    if year_match:
        year = int(year_match.group(1))
        text = text[: year_match.start()] + " " + text[year_match.end() :]
    # A trailing "-GROUP" is the release name, not part of the title.
    text = re.sub(r"\s-\s*\S+$", "", text)
    title = " ".join(text.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ").split())
    title = title.strip(" -")
    if not title:
        title = stem
    return title, year, season, episode


def siblings(video: Path) -> tuple[tuple[Path, ...], Path | None]:
    """Subtitle and artwork files that sit next to a video."""
    try:
        entries = list(video.parent.iterdir())
    except OSError:
        return (), None
    stem = video.stem.casefold()
    subs: list[Path] = []
    artwork: Path | None = None
    for entry in entries:
        if not entry.is_file():
            continue
        suffix = entry.suffix.casefold()
        name = entry.stem.casefold()
        if suffix in SUBTITLE_EXTENSIONS and (name.startswith(stem) or name.startswith("sub")):
            subs.append(entry)
        elif suffix in ARTWORK_EXTENSIONS and artwork is None:
            if any(name.startswith(candidate) for candidate in POSTER_NAMES):
                artwork = entry
    return tuple(sorted(subs)), artwork


def scan(
    roots: Iterable[Path | str],
    *,
    exclude_dirs: Sequence[str] = (),
    exclude_globs: Sequence[str] = (),
    max_files: int = MAX_FILES,
    max_depth: int = MAX_DEPTH,
) -> ScanResult:
    """Walk the chosen folders and return the playable files found."""
    result = ScanResult()
    skip_dirs = {item.casefold() for item in exclude_dirs}
    globs = [pattern for pattern in exclude_globs if pattern]
    for root in roots:
        allowed, why = guard_root(root)
        if not allowed:
            result.errors.append(f"{root}: {why}")
            continue
        base = Path(root)
        base_depth = len(base.parts)
        for current, dirnames, filenames in os.walk(base, followlinks=False):
            here = Path(current)
            depth = len(here.parts) - base_depth
            dirnames[:] = [
                name for name in sorted(dirnames)
                if depth < max_depth and name.casefold() not in skip_dirs and not name.startswith(".")
            ]
            for name in sorted(filenames):
                path = here / name
                if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                    continue
                if any(fnmatch.fnmatch(name, pattern) for pattern in globs):
                    result.skipped += 1
                    continue
                if len(result.files) >= max_files:
                    result.truncated = True
                    break
                title, year, season, episode = parse_name(name)
                subs, artwork = siblings(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    result.errors.append(f"{path}: could not be read.")
                    continue
                result.files.append(
                    MediaFile(
                        path=path,
                        size=size,
                        title=title,
                        year=year,
                        season=season,
                        episode=episode,
                        subtitles=subs,
                        artwork=artwork,
                    )
                )
            if result.truncated:
                break
        if result.truncated:
            break
    return result


def _fold(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def match(files: Iterable[MediaFile], candidates: Iterable[Mapping[str, Any]]) -> list[tuple[MediaFile, Mapping[str, Any] | None]]:
    """Pair files with catalogue metadata by title and, when both have one, year."""
    pool = [item for item in candidates if isinstance(item, Mapping)]
    out: list[tuple[MediaFile, Mapping[str, Any] | None]] = []
    for item in files:
        wanted = _fold(item.title)
        best: Mapping[str, Any] | None = None
        best_score = 0
        for candidate in pool:
            title = _fold(candidate.get("title"))
            if not title:
                continue
            if title == wanted:
                score = 3
            elif title.startswith(wanted) or wanted.startswith(title):
                score = 2
            elif wanted and wanted in title:
                score = 1
            else:
                continue
            year = candidate.get("year")
            if item.year and year:
                try:
                    score += 1 if int(str(year)[:4]) == item.year else -1
                except (TypeError, ValueError):
                    pass
            if score > best_score:
                best, best_score = candidate, score
        out.append((item, best if best_score > 0 else None))
    return out


def missing(known: Iterable[Path | str], present: Iterable[Path | str]) -> list[Path]:
    """Paths that were in the library before and are not on disk now."""
    have = {str(Path(item)) for item in present}
    return [Path(item) for item in known if str(Path(item)) not in have]


__all__ = [
    "ARTWORK_EXTENSIONS",
    "MAX_DEPTH",
    "MAX_FILES",
    "MediaFile",
    "POSTER_NAMES",
    "SUBTITLE_EXTENSIONS",
    "ScanResult",
    "VIDEO_EXTENSIONS",
    "guard_root",
    "match",
    "missing",
    "parse_name",
    "scan",
    "siblings",
]
