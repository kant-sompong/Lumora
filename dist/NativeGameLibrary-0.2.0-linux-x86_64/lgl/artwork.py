"""Attach a metadata provider's artwork and details to an imported record.

Matching is by name, and a name is not an identity: two games can share one, and
a repack title carries version and platform suffixes ("Happy Wheels
(v1.99.1-s) [Linux, Archive]") that no provider uses.  So this module never
guesses.  A result is applied only when exactly one candidate has the same
cleaned title (and the same year, when both are known); every other case is
returned for a person to choose from.

Nothing already stored is overwritten.  A second sync cannot replace a longer
description or a different poster with a provider's shorter one, and a record
whose only links failed a check does not become "downloadable" because artwork
was added.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .catalog_store import CatalogStore
from .sources import SourceError, SourceGame

#: Hosts that serve a provider's images.  They are approved for the record's
#: source only after a sync actually used one.
ARTWORK_HOSTS = (
    "media.rawg.io",
    "rawg.io",
    # Steam's image CDN, which the keyless fallback uses (and which the Kryoto
    # source already declares for the artwork its own records carry).
    "cdn.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com",
    "shared.akamai.steamstatic.com",
    "shared.fastly.steamstatic.com",
)
#: How many gameplay screenshots are kept.
MAX_SCREENSHOTS = 6
#: Concurrent lookups a bulk sync may keep in flight.  The provider is a plain
#: HTTP API, so a handful of requests in parallel turns a serial 2-request-per
#: game crawl into minutes instead of hours without flooding it.
SYNC_WORKERS = 8
#: How long "the provider has no match for this title" is trusted, so a second
#: sync does not re-ask about every game that will never have a poster.
MISS_TTL = 7 * 24 * 3600.0
#: Bumped whenever the matching rules change: a "no match" recorded under older
#: rules is not evidence about the new ones, so it is retried instead of trusted.
#: 3 added the keyless Steam store fallback, so every miss recorded by a
#: RAWG-only run has to be asked again.
MATCH_RULES = 3

_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_NOISE = re.compile(r"[^0-9a-z]+")
#: Roman numerals as whole words, folded to digits so "Red Faction II" and
#: "Red Faction 2" compare equal.  Single letters (i, v, x, l, c, d, m) are left
#: alone: "Mega Man X" is a letter, not a ten.
_ROMANS = {
    "ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7",
    "viii": "8", "ix": "9", "xi": "11", "xii": "12",
}
_ROMAN_WORD = re.compile(r"\b(ii|iii|iv|vi|vii|viii|ix|xi|xii)\b")

#: Core fields filled when empty, with the label shown in the confirmation.
#: ``cover_url`` is handled separately: a poster whose host cannot be fetched is
#: not usable data, so it is repaired rather than preserved.
_FIELD_LABELS = (
    ("description", "description"),
    ("developer", "developer"),
    ("publisher", "publisher"),
    ("release_date", "release date"),
    ("year", "year"),
)


def clean_title(value: Any) -> str:
    """A comparison key: no suffixes, no punctuation, no case.

    ``"Happy Wheels (v1.99.1-s) [Linux, Archive]"`` and ``"Happy Wheels"`` both
    become ``"happy wheels"``, which is the common case this has to handle.
    Roman numerals are folded to digits, so ``"Red Faction II"`` and ``"Red
    Faction 2"`` also meet.
    """
    text = _BRACKETS.sub(" ", str(value or ""))
    key = _NOISE.sub(" ", text.casefold()).strip()
    return _ROMAN_WORD.sub(lambda match: _ROMANS[match.group(1)], key)


def cover_usable(record: CatalogGame, source: Any) -> bool:
    """True when the window would actually be allowed to fetch this poster.

    The artwork loader only accepts ``https`` on the archive hosts or on hosts
    the record's own source named.  A poster anywhere else is dead weight: it
    can never be displayed, so treating it as missing is not a downgrade.
    """
    url = str(getattr(record, "cover_url", "") or "")
    if not url:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if host == "archive.org" or host.endswith(".archive.org"):
        return True
    approved = list(getattr(source, "image_hosts", None) or ())
    return any(host == allowed or host.endswith("." + allowed) for allowed in approved)


def needs_artwork(store: CatalogStore, record: CatalogGame) -> bool:
    """True when this record has no poster the app could show."""
    return not cover_usable(record, store.get_source(record.source_id))


def _title_key(value: Any) -> str:
    return clean_title(value)


def _decorated(title: str) -> bool:
    """True when a title carries a parenthetical suffix like "(itch)" or "(1993)"."""
    return bool(re.search(r"[\(\[\{]", str(title or "")))


def remembered_miss(record: CatalogGame, now: float | None = None) -> bool:
    """True when a recent sync searched this exact title and found nothing.

    A provider that has no match for a game will usually still have no match a
    minute later, so a bulk run skips it and saves the requests.  The memory
    expires, and any change to the title makes it stale immediately.
    """
    extra = record.extra if isinstance(getattr(record, "extra", None), Mapping) else {}
    miss = extra.get("artwork_miss")
    if not isinstance(miss, Mapping):
        return False
    if miss.get("title") != _title_key(getattr(record, "title", "")):
        return False
    if miss.get("rules") != MATCH_RULES:
        # Recorded before a matching-rule change: try again under this one.
        return False
    try:
        searched_at = float(miss.get("searched_at") or 0.0)
    except (TypeError, ValueError):
        return False
    age = (now if now is not None else time.time()) - searched_at
    return 0.0 <= age < MISS_TTL


def remember_miss(
    store: CatalogStore, game_key: str, *, now: float | None = None
) -> None:
    """Record that a lookup for this record's current title had no match."""
    record = store.get_game(game_key)
    if record is None:
        return
    extra = dict(record.extra) if isinstance(record.extra, Mapping) else {}
    extra["artwork_miss"] = {
        "title": _title_key(record.title),
        "rules": MATCH_RULES,
        "searched_at": now if now is not None else time.time(),
    }
    record.extra = extra


@dataclass(frozen=True)
class Candidate:
    """One provider result that could be the game a record describes."""

    provider: str
    game_id: str
    title: str
    year: str = ""
    release_date: str = ""
    developer: str = ""
    publisher: str = ""
    genres: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    description: str = ""
    cover_url: str = ""
    page_url: str = ""
    screenshots: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return clean_title(self.title)

    @classmethod
    def from_game(cls, game: SourceGame) -> "Candidate":
        return cls(
            provider=game.source_id,
            game_id=game.game_id,
            title=game.title,
            year=game.year,
            release_date=game.release_date,
            developer=game.developer,
            publisher=game.publisher,
            genres=tuple(game.genres),
            platforms=tuple(game.platforms),
            description=game.description,
            cover_url=game.cover_url,
            page_url=game.source_url,
        )

    def describe(self) -> str:
        """One line for a picker, with no claim beyond what the provider said."""
        bits = [self.title]
        if self.year:
            bits.append(f"({self.year})")
        byline = self.developer or self.publisher
        if byline:
            bits.append("· " + byline)
        return " ".join(bits)


def find_candidates(provider: Any, title: str, *, limit: int = 8) -> list[Candidate]:
    """Ask the provider for records whose name resembles *title*."""
    games = provider.search_games(title, limit=limit)
    return [
        Candidate.from_game(game)
        for game in games
        if getattr(game, "title", "") and getattr(game, "game_id", "")
    ]


def rank_candidates(
    candidates: Sequence[Candidate], title: str, year: str = ""
) -> list[Candidate]:
    """Best-first: same cleaned title, then same year, then title order."""
    wanted = clean_title(title)
    wanted_year = str(year or "").strip()[:4]

    def order(candidate: Candidate) -> tuple[int, int, str, str]:
        same_title = 0 if candidate.key == wanted else 1
        same_year = 0 if wanted_year and candidate.year == wanted_year else 1
        return (same_title, same_year, candidate.title.casefold(), candidate.game_id)

    return sorted(candidates, key=order)


def _best_of(
    group: Sequence[Candidate], wanted_year: str = ""
) -> Candidate:
    """The least surprising entry of one name group: plain, dated, with art."""

    def order(candidate: Candidate) -> tuple[int, int, int, str, str]:
        return (
            0 if wanted_year and candidate.year == wanted_year else 1,
            0 if not _decorated(candidate.title) else 1,
            0 if candidate.cover_url else 1,
            candidate.title.casefold(),
            candidate.game_id,
        )

    return sorted(group, key=order)[0]


#: Words that only name an edition of a game, never a different game.  They
#: are the *only* thing a title may add to a provider's title and still match.
_EDITION_WORDS = frozenset(
    """
    deluxe definitive enhanced complete edition goty game of the year collector
    collectors remastered remaster anniversary ultimate gold platinum special
    season
    """.split()
)


def _resolve(
    group: Sequence[Candidate], title: str, year: str = ""
) -> Candidate | None:
    """Decide which entry of one same-name group is *this* record, if any."""
    wanted_year = str(year or "").strip()[:4]
    raw = str(title or "").strip().casefold()

    # Strongest signal: the provider's own title is character-for-character the
    # record's title, decorations included ("... Blade of Destiny (Remake)").
    exact = [c for c in group if c.title.strip().casefold() == raw]
    if len(exact) == 1:
        return exact[0]
    if exact and wanted_year:
        dated = [c for c in exact if c.year == wanted_year]
        if dated:
            return _best_of(dated, wanted_year)
    if wanted_year:
        dated = [c for c in group if c.year == wanted_year]
        if dated:
            return _best_of(dated, wanted_year)
    # The undecorated entry is the canonical one: "Saboteur!" over "Saboteur".
    plain = [c for c in group if not _decorated(c.title)]
    if len(plain) == 1:
        return plain[0]
    years = {c.year for c in group if c.year}
    if len(years) == 1:
        # Same name, one year: re-releases or another storefront's listing.
        return _best_of(group, wanted_year)
    # Two undated entries, or dated entries with no year to separate them: the
    # page does not say which game this is.
    return None


def pick_candidate(
    candidates: Sequence[Candidate], title: str, year: str = ""
) -> Candidate | None:
    """The single unambiguous match for *title*, or ``None`` when there is none.

    A provider often lists one game several times - ``"Rail Route"``,
    ``"Rail Route (itch)"``, ``"Rail Route (2018)"`` - and those are one game,
    not a conflict, so a group like that resolves to its canonical entry.  A
    name shared by *different* games (``"Prey"`` 2006 and 2017) is still
    reported as ``None`` unless the year or the exact title separates them: the
    first result is not evidence of the right one.
    """
    wanted = clean_title(title)
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.key, []).append(candidate)

    matches = groups.get(wanted)
    if matches:
        return _resolve(matches, title, year)

    # The record may carry an edition suffix the provider does not
    # ("... : Definitive Edition").  Extra words are accepted only when every
    # one of them names an edition - anything else is a different game.
    words = wanted.split()

    def edition_matches(longer: str, shorter: str) -> bool:
        """True when *longer* is *shorter* plus nothing but edition words."""
        if not longer.startswith(shorter + " "):
            return False
        extra = longer.split()[len(shorter.split()):]
        return bool(extra) and all(word in _EDITION_WORDS for word in extra)

    def only_one(keys: list[str]) -> str | None:
        """The longest key, when it is the obvious one."""
        if not keys:
            return None
        longest = max(len(key) for key in keys)
        best = [key for key in keys if len(key) == longest]
        return best[0] if len(best) == 1 else None

    # The record adds an edition word the provider does not: "X GOTY" -> "X".
    key = only_one([k for k in groups if edition_matches(wanted, k)])
    if key is not None:
        return _resolve(groups[key], title, year)
    # The provider adds one: "SpellForce Platinum" -> "SpellForce - Platinum
    # Edition".  Only after the shorter reading, so an edition entry cannot
    # win over the plain game.
    key = only_one([k for k in groups if edition_matches(k, wanted)])
    if key is not None:
        return _resolve(groups[key], title, year)
    return None


def search_titles(title: str, *, limit: int = 3) -> tuple[str, ...]:
    """Queries to try for one record, most precise first.

    Providers are picky about a decorated title: a store search for "THIEF:
    Definitive Edition" answers with nothing while "THIEF" finds the game.
    Only the *query* is relaxed here - whatever comes back still has to match
    the record's own title under :func:`pick_candidate`, so a broader search
    cannot attach the wrong game's poster.
    """
    raw = " ".join(str(title or "").split())
    if not raw:
        return ()
    without_suffixes = " ".join(_BRACKETS.sub(" ", raw).split())
    queries = [raw, without_suffixes]
    for separator in (":", " - ", " – ", " — "):
        head = without_suffixes.split(separator)[0].strip(" -:–—")
        if head:
            queries.append(head)
    kept: list[str] = []
    seen: set[str] = set()
    for query in queries:
        folded = query.casefold()
        if query and folded not in seen:
            seen.add(folded)
            kept.append(query)
    return tuple(kept[:limit])


class ArtworkSearchers:
    """Try each artwork provider in turn, skipping one that refused outright.

    RAWG carries the richer metadata but a monthly quota; the Steam store needs
    no key.  A provider that answered 401/403/429 is not asked again during the
    same run - the refusal is kept, the next provider answers, and the run only
    stops when *every* provider has refused.  A provider that simply has no
    match does not silence the others.
    """

    def __init__(self, providers: Sequence[Any]) -> None:
        self.providers = [provider for provider in providers if provider is not None]
        self._refused: dict[str, SourceError] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _name(provider: Any) -> str:
        return str(getattr(provider, "id", "") or type(provider).__name__)

    def refused(self) -> list[SourceError]:
        """The refusals seen so far, so the run can say why it stopped."""
        with self._lock:
            return list(self._refused.values())

    @property
    def exhausted(self) -> bool:
        """True when every provider refused: no request can succeed now."""
        with self._lock:
            return bool(self.providers) and len(self._refused) >= len(self.providers)

    def choose(self, title: str, year: str = "", *, limit: int = 20) -> Candidate | None:
        """The single unambiguous match for *title* from any provider."""
        for provider in self.providers:
            name = self._name(provider)
            with self._lock:
                if name in self._refused:
                    continue
            for query in search_titles(title):
                try:
                    games = provider.search_games(query, limit=limit)
                except SourceError as exc:
                    if getattr(exc, "stops_run", False):
                        with self._lock:
                            self._refused.setdefault(name, exc)
                    break
                except (OSError, ValueError):
                    break
                usable = [
                    game for game in (games or ())
                    if getattr(game, "game_id", "") and getattr(game, "title", "")
                ]
                if not usable:
                    continue
                candidates = [Candidate.from_game(game) for game in usable]
                chosen = pick_candidate(
                    rank_candidates(candidates, title, year), title, year
                )
                if chosen is None:
                    continue
                settle = getattr(provider, "with_cover", None)
                if callable(settle):
                    # A provider that only advertises a cover URL (Steam) checks
                    # the CDN before claiming it; a dead image URL would hide the
                    # record from every later sync.
                    chosen = settle(chosen)
                return chosen
        return None


def with_screenshots(provider: Any, candidate: Candidate) -> Candidate:
    """Ask for gameplay screenshots; failing that is not a failure to sync."""
    try:
        shots = provider.screenshots(candidate.game_id)
    except (SourceError, OSError, ValueError, KeyError):
        shots = ()
    kept = [
        url for url in (shots or ())
        if isinstance(url, str) and url.startswith("https://")
    ]
    return replace(candidate, screenshots=tuple(dict.fromkeys(kept))[:MAX_SCREENSHOTS])


def apply_candidate(
    store: CatalogStore,
    game_key: str,
    candidate: Candidate,
    *,
    now: float | None = None,
    replace_cover: bool = False,
) -> list[str]:
    """Fill the record's empty fields from *candidate*; return what was added.

    ``replace_cover`` is for an explicit request about one game: the person
    asked for this poster, so it wins over the stored one.  Without it, a
    poster is only replaced when it could never be displayed (see
    :func:`cover_usable`), so a bulk run cannot churn good artwork.

    The provider result is stored under ``extra["artwork"]`` so the origin of
    the poster stays visible, and its image host is approved for this source so
    the window may fetch the image at all.
    """
    record = store.get_game(game_key)
    if record is None:
        raise SourceError(f"{game_key} is not in the catalog any more.")
    source = store.get_source(record.source_id)
    filled: list[str] = []

    replaced_cover = ""
    if candidate.cover_url and candidate.cover_url != record.cover_url:
        # An explicit sync wins; otherwise keep a poster that can be shown and
        # repair only one that could never load.
        if replace_cover or not cover_usable(record, source):
            replaced_cover = record.cover_url
            record.cover_url = candidate.cover_url
            filled.append("poster")

    for name, label in _FIELD_LABELS:
        value = getattr(candidate, name, "")
        if value and not getattr(record, name, ""):
            setattr(record, name, value)
            filled.append(label)
    if candidate.genres and not record.genres:
        record.genres = tuple(candidate.genres)
        filled.append("genres")
    if candidate.platforms and not record.platforms:
        record.platforms = tuple(candidate.platforms)
        filled.append("platforms")

    extra = dict(record.extra) if isinstance(record.extra, Mapping) else {}
    artwork = {
        "provider": candidate.provider,
        "id": candidate.game_id,
        "title": candidate.title,
        "url": candidate.page_url,
        "fetched_at": now if now is not None else time.time(),
    }
    if replaced_cover:
        # Keep what was there, so a repair can be explained and undone.
        artwork["previous_cover"] = replaced_cover
    extra["artwork"] = artwork
    # A poster was found, so a remembered "no match" is no longer true.
    extra.pop("artwork_miss", None)
    shots = [
        url for url in candidate.screenshots
        if isinstance(url, str) and url.startswith("https://")
    ]
    if shots and extra.get("screenshots") != shots:
        extra["screenshots"] = shots
        filled.append("gameplay screenshots")
    record.extra = extra
    record.updated_at = time.time()

    if source is not None:
        for host in ARTWORK_HOSTS:
            if host not in source.image_hosts:
                source.image_hosts.append(host)
    return filled


__all__ = [
    "ARTWORK_HOSTS",
    "MAX_SCREENSHOTS",
    "MATCH_RULES",
    "MISS_TTL",
    "SYNC_WORKERS",
    "ArtworkSearchers",
    "search_titles",
    "remembered_miss",
    "remember_miss",
    "Candidate",
    "apply_candidate",
    "clean_title",
    "cover_usable",
    "find_candidates",
    "needs_artwork",
    "pick_candidate",
    "rank_candidates",
    "with_screenshots",
]
