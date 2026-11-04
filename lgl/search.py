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
KINDS = ("game", "movie", "tv", "collection")
SORTS = ("relevance", "title", "year")
CACHE_TTL = 30.0
MAX_CACHE = 32


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

    error: str = ""

    @property
    def empty(self) -> bool:
        return self.total == 0

    def as_dict(self) -> dict[str, int]:
        return {
            "games": len(self.games),
            "movies": len(self.movies),
            "tv": len(self.tv),
            "collections": len(self.collections),
        }


def _game_fields(entry: Any) -> list[str]:
    def read(name: str) -> str:
        value = entry.get(name) if isinstance(entry, dict) else getattr(entry, name, "")
        return str(value or "")

    return [read("title"), read("creator"), read("identifier")]


def _media_fields(entry: Any) -> list[str]:
    title = entry.get("title") if isinstance(entry, dict) else getattr(entry, "title", "")
    year = entry.get("year") if isinstance(entry, dict) else getattr(entry, "year", "")
    return [str(title or ""), str(year or "")]


def _media_kind(entry: Any) -> str:
    raw = entry.get("kind") if isinstance(entry, dict) else getattr(entry, "kind", "movie")
    return str(raw or "movie")


def _best(haystacks: Iterable[str], needle: str) -> int:
    return max((score(fold(text), needle) for text in haystacks), default=0)


@dataclass(frozen=True)
class Filters:
    """Narrowing options shown beside the box."""

    kinds: tuple[str, ...] = KINDS
    favorites_only: bool = False
    year_from: int | None = None
    year_to: int | None = None
    genre: str = ""

    def allows(self, kind: str) -> bool:
        return kind in self.kinds

    @property
    def active(self) -> bool:
        return bool(
            set(self.kinds) != set(KINDS)
            or self.favorites_only
            or self.year_from is not None
            or self.year_to is not None
            or self.genre
        )

    def key(self) -> tuple[Any, ...]:
        return (
            tuple(sorted(str(k) for k in self.kinds)),
            self.favorites_only,
            self.year_from,
            self.year_to,
            fold(self.genre),
        )


def _field(entry: Any, name: str, default: Any = None) -> Any:
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _year(entry: Any) -> int | None:
    raw = _field(entry, "year")
    try:
        value = int(str(raw).strip()[:4])
    except (TypeError, ValueError):
        return None
    return value if 1800 <= value <= 2200 else None


def _identity(entry: Any) -> str:
    for name in ("identifier", "key", "id"):
        value = _field(entry, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _passes(entry: Any, filters: Filters, favorites: set[str]) -> bool:
    if filters.favorites_only and _identity(entry) not in favorites:
        return False
    if filters.year_from is not None or filters.year_to is not None:
        year = _year(entry)
        if year is None:
            return False
        if filters.year_from is not None and year < int(filters.year_from):
            return False
        if filters.year_to is not None and year > int(filters.year_to):
            return False
    genre = fold(filters.genre)
    if genre and genre not in fold(_field(entry, "genre", "")):
        return False
    return True


def _sorted(items: list[Any], ranks: dict[int, int], sort: str) -> list[Any]:
    if sort == "title":
        return sorted(items, key=lambda e: fold(_field(e, "title", "")))
    if sort == "year":
        return sorted(
            items,
            key=lambda e: (-(_year(e) or 0), fold(_field(e, "title", ""))),
        )
    return sorted(items, key=lambda e: (-ranks[id(e)], fold(_field(e, "title", ""))))


class SearchCache:
    """Tiny TTL cache so retyping a query does not re-run the matcher."""

    def __init__(self, ttl: float = CACHE_TTL, *, clock: Any = None, max_entries: int = MAX_CACHE):
        import time as _time

        self.ttl = max(0.0, float(ttl))
        self.clock = clock or _time.monotonic
        self.max_entries = max(1, int(max_entries))
        self._items: dict[tuple[Any, ...], tuple[float, Results]] = {}

    def key(self, query: str, filters: Filters, sort: str, revision: Any = "") -> tuple[Any, ...]:
        return (fold(query), filters.key(), sort, str(revision))

    def get(self, key: tuple[Any, ...]) -> Results | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        at, value = entry
        if self.ttl and self.clock() - at > self.ttl:
            self._items.pop(key, None)
            return None
        return value

    def put(self, key: tuple[Any, ...], value: Results) -> None:
        if len(self._items) >= self.max_entries:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
        self._items[key] = (self.clock(), value)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


class Debouncer:
    """Decide whether a keystroke should trigger work now or later."""

    def __init__(self, delay: float = 0.25, *, clock: Any = None):
        import time as _time

        self.delay = max(0.0, float(delay))
        self.clock = clock or _time.monotonic
        self._last = 0.0
        self._started = False
        self._pending = False

    def keystroke(self) -> bool:
        """Record input; return True when the caller should run the query."""
        now = self.clock()
        if self._started and now - self._last < self.delay:
            self._pending = True
            self._last = now
            return False
        self._last = now
        self._started = True
        self._pending = False
        return True

    def flush(self) -> bool:
        """True if input arrived that has not been acted on yet."""
        pending = self._pending
        self._pending = False
        return pending


def describe(results: Results, *, provider_error: str = "") -> str:
    """Empty/error state copy for the results pane."""
    if results.error:
        return results.error
    if results.empty:
        if provider_error:
            return f"No local matches for “{results.query}”. {provider_error}"
        if results.query:
            return f"No matches for “{results.query}”."
        return "Type to search games, films, shows and collections."
    return ""


def search(
    query: str,
    *,
    entries: Sequence[Any] = (),
    media: Sequence[Any] = (),
    collections: Sequence[Any] = (),
    limit: int = DEFAULT_LIMIT,
    filters: Filters | None = None,
    sort: str = "relevance",
    favorites: Iterable[str] = (),
) -> Results:
    """Match ``query`` against games, media and collections.

    An empty query returns empty groups rather than everything: the box is for
    narrowing, not for browsing.
    """
    needle = fold(query)
    results = Results(query=str(query or ""))
    if not needle:
        return results
    filters = filters or Filters()
    sort = sort if sort in SORTS else "relevance"
    favorite_set = {str(item) for item in favorites}
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = DEFAULT_LIMIT
    if cap == 0:
        return results

    if filters.allows("game"):
        hits: list[Any] = []
        ranks: dict[int, int] = {}
        for entry in entries:
            rank = _best(_game_fields(entry), needle)
            if rank and _passes(entry, filters, favorite_set):
                hits.append(entry)
                ranks[id(entry)] = rank
        results.games = _sorted(hits, ranks, sort)[:cap]

    if filters.allows("movie") or filters.allows("tv"):
        for bucket in ("movie", "tv"):
            hits = []
            ranks = {}
            for entry in media:
                if _media_kind(entry) != bucket:
                    continue
                rank = _best(_media_fields(entry), needle)
                if rank and _passes(entry, filters, favorite_set):
                    hits.append(entry)
                    ranks[id(entry)] = rank
            target = results.movies if bucket == "movie" else results.tv
            if filters.allows(bucket):
                target.extend(_sorted(hits, ranks, sort)[:cap])

    if filters.allows("collection"):
        hits = []
        ranks = {}
        for collection in collections:
            name = str(_field(collection, "name", "") or "")
            rank = _best([name], needle)
            if rank and _passes(collection, filters, favorite_set):
                hits.append(collection)
                ranks[id(collection)] = rank
        results.collections = _sorted(hits, ranks, sort)[:cap]
    return results


__all__ = [
    "CACHE_TTL",
    "Debouncer",
    "DEFAULT_LIMIT",
    "Filters",
    "KINDS",
    "MAX_CACHE",
    "Results",
    "SORTS",
    "SearchCache",
    "describe",
    "fold",
    "score",
    "search",
]
