"""Filtering and sorting for the Qt Browse view.

The same rules live in ``ui.py`` for the GTK frontend.  They are kept as plain
functions (no widget imports) so the two frontends cannot disagree about what a
"1990s" game is, and so they can be unit-tested without a display.
"""

from __future__ import annotations

import urllib.parse
from typing import Sequence

from .archive import ArchiveClient, GameSummary
from .catalog_store import DOWNLOAD_STATE_LABELS
from .settings import ARCHIVE_SORT_KEYS
from .util import human_size

SORT_LABELS = {
    "title": "Title",
    "year": "Year",
    "size": "Size",
    "downloads": "Downloads",
    "downloadable": "Download",
    "creator": "Creator",
    "genre": "Genre",
}

YEAR_FILTERS = (
    ("all", "All years"),
    ("pre1990", "Before 1990"),
    ("1990s", "1990s"),
    ("2000s", "2000s"),
    ("2010s", "2010s"),
    ("2020s", "2020s and later"),
)
LICENSE_FILTERS = (
    ("all", "All licenses"),
    ("publicdomain", "Public domain"),
    ("cc", "Creative Commons"),
    ("other", "Other license"),
    ("none", "No license listed"),
)
SIZE_FILTERS = (
    ("any", "Any"),
    ("10", "< 10 GB"),
    ("50", "< 50 GB"),
    ("100", "< 100 GB"),
)
#: Suffixes/formats that count for the compact "File format" group.
FORMAT_FILTERS = (
    ("all", "All formats"),
    ("zip", "Zip archive"),
    ("7z", "7-Zip archive"),
    ("rar", "RAR archive"),
    ("tar", "Tar archive"),
    ("disc", "Disc image"),
    ("package", "Package / AppImage"),
    ("shell", "Shell installer"),
)

_FORMAT_SUFFIXES = {
    "zip": (".zip",),
    "7z": (".7z",),
    "rar": (".rar",),
    "tar": (".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar.zst"),
    "disc": (".iso", ".bin", ".cue", ".img", ".mdf", ".nrg"),
    "package": (".appimage", ".deb", ".rpm", ".flatpak", ".snap", ".pkg.tar.zst"),
    "shell": (".sh", ".run", ".bin"),
}


def license_group(url: str) -> str:
    text = (url or "").lower()
    if not text:
        return "none"
    if "publicdomain" in text or "public-domain" in text or "mark" in text:
        return "publicdomain"
    if "creativecommons" in text or "/licenses/" in text:
        return "cc"
    return "other"


def year_matches(year: str, bucket: str) -> bool:
    if bucket == "all":
        return True
    try:
        value = int(str(year)[:4])
    except (TypeError, ValueError):
        return False
    return {
        "pre1990": value < 1990,
        "1990s": 1990 <= value < 2000,
        "2000s": 2000 <= value < 2010,
        "2010s": 2010 <= value < 2020,
        "2020s": value >= 2020,
    }.get(bucket, True)


def size_matches(size: int | None, bucket: str) -> bool:
    """``Any`` keeps unknown sizes; the caps only match a known small size."""
    if bucket == "any":
        return True
    if not size:
        return False
    try:
        limit = int(bucket) * 1024 ** 3
    except (TypeError, ValueError):
        return True
    return size < limit


def format_matches(names: Sequence[str], bucket: str) -> bool:
    if bucket == "all":
        return True
    suffixes = _FORMAT_SUFFIXES.get(bucket, ())
    return any(str(name).lower().endswith(suffixes) for name in names)


def bare_title(title: object) -> str:
    """``Doom (v1.9) [Linux]`` -> ``Doom``."""
    text = " ".join(str(title or "").split())
    for marker in (" (", " [", " {"):
        head, sep, _tail = text.partition(marker)
        if sep and head.strip():
            text = head.strip()
    return text


def tile_meta(game: GameSummary) -> str:
    bits: list[str] = []
    if game.item_size:
        bits.append(human_size(game.item_size))
    if game.year:
        bits.append(str(game.year))
    if game.is_catalog:
        state = DOWNLOAD_STATE_LABELS.get(game.download_state)
        if state:
            bits.append(state)
    return " · ".join(bits)


def thumb_url(game: GameSummary) -> str:
    if game.cover_url:
        return game.cover_url
    if not game.identifier or game.identifier.startswith("catalog:"):
        return ""
    return "https://archive.org/services/img/" + urllib.parse.quote(game.identifier, safe="")


def filter_games(
    games: Sequence[GameSummary],
    *,
    query: str = "",
    year: str = "all",
    license_: str = "all",
    genre: str = "all",
    genres: Sequence[str] = (),
    size: str = "any",
) -> list[GameSummary]:
    out = ArchiveClient.search(list(games), query) if query.strip() else list(games)
    if year != "all":
        out = [g for g in out if year_matches(g.year, year)]
    if license_ != "all":
        out = [g for g in out if license_group(g.licenseurl) == license_]
    wanted = {value.casefold() for value in ([genre] if genre != "all" else [])}
    wanted |= {value.casefold() for value in genres}
    if wanted:
        out = [
            g for g in out
            if wanted & {subject.casefold() for subject in g.subjects}
        ]
    if size != "any":
        out = [g for g in out if size_matches(g.item_size, size)]
    return out


def _num(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _year_num(game: GameSummary) -> int:
    try:
        return int(str(game.year)[:4])
    except (TypeError, ValueError):
        return 0


def sort_games(games: Sequence[GameSummary], key: str, descending: bool) -> list[GameSummary]:
    """Sort with missing values last in both directions; ties fall back to title."""
    if key not in ARCHIVE_SORT_KEYS:
        key = "title"

    def value(game: GameSummary) -> object:
        if key == "year":
            return _year_num(game) or None
        if key == "size":
            return _num(game.item_size) or None
        if key == "downloads":
            return _num(game.downloads) or None
        if key == "downloadable":
            return None if game.is_catalog and game.download_state in ("none", "broken") else 1
        if key == "creator":
            return (game.creator or "").casefold() or None
        if key == "genre":
            return (game.subjects[0].casefold() if game.subjects else "") or None
        return (game.title or "").casefold()

    present = [g for g in games if value(g) is not None]
    missing = [g for g in games if value(g) is None]
    present.sort(key=lambda g: (g.title or "").casefold())  # stable tie-break
    present.sort(key=value, reverse=descending)  # type: ignore[arg-type,return-value]
    missing.sort(key=lambda g: (g.title or "").casefold())
    return present + missing


def genre_counts(games: Sequence[GameSummary], limit: int = 12) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for game in games:
        for subject in game.subjects:
            folded = subject.casefold()
            counts[folded] = counts.get(folded, 0) + 1
            labels.setdefault(folded, subject)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [(labels[k], n) for k, n in ranked]


def genre_total(games: Sequence[GameSummary]) -> int:
    """How many distinct genres the collection lists, for the "All 89" header."""
    return len({subject.casefold() for game in games for subject in game.subjects})


__all__ = [
    "FORMAT_FILTERS",
    "LICENSE_FILTERS",
    "SIZE_FILTERS",
    "SORT_LABELS",
    "YEAR_FILTERS",
    "bare_title",
    "filter_games",
    "format_matches",
    "genre_counts",
    "genre_total",
    "license_group",
    "size_matches",
    "sort_games",
    "thumb_url",
    "tile_meta",
    "year_matches",
]
