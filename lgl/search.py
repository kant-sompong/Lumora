"""One search box for everything the app can show.

Games, films, shows and collections are matched in memory against a folded
string — case, accents and spacing removed — so typing never waits on the
network and an unreachable provider cannot break results that are stored
locally.  Nothing in this module fetches anything.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

DEFAULT_LIMIT = 8


def fold(text: Any) -> str:
    """Case-, accent- and punctuation-insensitive form used for matching."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def score(haystack: str, needle: str) -> int:
    """Rank one folded haystack against one folded needle; 0 means no match."""
    if not needle or not haystack:
        return 0
    if haystack == needle:
        return 100
    if haystack.startswith(needle):
        return 80
    if f" {needle}" in haystack:
        return 60
    if needle in haystack:
        return 40
    return 0


@dataclass
class Results:
    """Search hits grouped by what they are, so a card can say so."""

    query: str = ""
    games: list[Any] = field(default_factory=list)
    movies: list[Any] = field(default_factory=list)
    tv: list[Any] = field(default_factory=list)
    collections: list[Any] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.games) + len(self.movies) + len(self.tv) + len(self.collections)

    @property
    def empty(self) -> bool:
        return self.total == 0


def _game_fields(entry: Any) -> list[str]:
    return [
        str(getattr(entry, "title", "") or ""),
        str(getattr(entry, "creator", "") or ""),
        str(getattr(entry, "identifier", "") or ""),
    ]


def _media_fields(entry: Any) -> list[str]:
    if isinstance(entry, dict):
        return [str(entry.get("title") or ""), str(entry.get("year") or "")]
    return [str(getattr(entry, "title", "") or "")]


def _media_kind(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("kind") or "movie")
    return str(getattr(entry, "kind", "movie") or "movie")


def _best(haystacks: Iterable[str], needle: str) -> int:
    return max((score(fold(text), needle) for text in haystacks), default=0)


def search(
    query: str,
    *,
    entries: Sequence[Any] = (),
    media: Sequence[Any] = (),
    collections: Sequence[Any] = (),
    limit: int = DEFAULT_LIMIT,
) -> Results:
    """Match ``query`` against games, media and collections.

    An empty query returns empty groups rather than everything: the box is for
    narrowing, not for browsing.
    """
    needle = fold(query)
    results = Results(query=str(query or ""))
    if not needle:
        return results
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = DEFAULT_LIMIT
    if cap == 0:
        return results

    ranked: list[tuple[int, str, Any]] = []
    for entry in entries:
        rank = _best(_game_fields(entry), needle)
        if rank:
            ranked.append((rank, fold(getattr(entry, "title", "")), entry))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    results.games = [item[2] for item in ranked[:cap]]

    for bucket in ("movie", "tv"):
        ranked = []
        for entry in media:
            if _media_kind(entry) != bucket:
                continue
            rank = _best(_media_fields(entry), needle)
            if rank:
                ranked.append((rank, fold(_media_fields(entry)[0]), entry))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        target = results.movies if bucket == "movie" else results.tv
        target.extend(item[2] for item in ranked[:cap])

    ranked = []
    for collection in collections:
        name = str(getattr(collection, "name", "") or "")
        rank = _best([name], needle)
        if rank:
            ranked.append((rank, fold(name), collection))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    results.collections = [item[2] for item in ranked[:cap]]
    return results


__all__ = ["DEFAULT_LIMIT", "Results", "fold", "score", "search"]
