"""MAL-authoritative anime identity, and the AniList id that ani.pm plays.

MyAnimeList is the only authority this module trusts for "is this catalogue
title anime".  Nothing else is used as proof: not the original language, not
the Animation genre, not a title that merely looks Japanese, and not a title
search that happens to return something.  A catalogue entry is anime only when
it is matched to a real MAL anime entry by the rules in
:func:`matches_catalogue`, and a title that cannot be matched is *unverified* -
which is not the same as "not anime", so callers must never hide it.

Two identifiers live here and never cross:

* ``mal_id`` - the MyAnimeList id of the matched entry (MyAnimeList authority).
* ``anilist_id`` - the AniList id, resolved **separately** after MAL verifies
  the title, because ani.pm keys playback by AniList id.

AniList resolution is evidence-based and validated: either AniList's own
``Media(idMal:)`` link, or a crosswalk candidate that is then checked against
AniList's ``idMal`` for that entry.  A candidate is never assumed, and a MAL id
is never passed where an AniList id is required - if AniList cannot be resolved
safely the result is ``RESOLUTION_FAILURE`` with the candidate ids, and the
playback layer must not invent one.

Official API notes (checked against MAL's own reference, not a third party):

* ``https://api.myanimelist.net/v2``, public reads (``/anime?q=``,
  ``/anime/{id}``) take the Client ID in an ``X-MAL-CLIENT-ID`` header; OAuth is
  only needed for a user's own list data.  The Client ID is per-application,
  registered on the MAL website, and is *not* something this code invents or
  ships: it comes from ``MAL_CLIENT_ID`` / the app's settings through
  :mod:`lgl.mal`.
* Rate limits are not documented publicly.  Community practice is at most about
  one request a second, and MAL can answer ``403`` ("DoS detected") rather than
  ``429``, so :class:`MalApiLookup` throttles every request and treats any
  non-answer as "lookup unavailable".
* MAL publishes no TMDB/IMDb crosswalk, so catalogue matching is done here by
  exact title (primary or alternative title) plus year and media type.  There
  is no fuzzy scoring: two entries that both match are ambiguous, not a coin
  toss.
* AniList's public GraphQL endpoint needs no key, and ``Media(idMal:)`` /
  ``Media.idMal`` are first-party link fields, which is why they are the
  trusted AniList resolution path.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from . import mal as mal_api
from . import platform_support

__all__ = [
    "CACHE_FILENAME",
    "DAY",
    "default_anilist_lookup",
    "default_mal_lookup",
    "GRAPHQL_URL",
    "MAL_MIN_INTERVAL",
    "AnimeIdentity",
    "AniListGraphQLLookup",
    "AniListLookup",
    "AniListResolution",
    "IdentityCache",
    "IdentityStatus",
    "LookupUnavailable",
    "MalApiLookup",
    "MalLookup",
    "MalMatch",
    "Reason",
    "crosswalk_anilist_ids",
    "identify",
    "identity_key",
    "identify_tmdb",
    "matches_catalogue",
    "resolve_anilist",
]

#: Kept beside the other caches, in ``cache_dir()``.
CACHE_FILENAME = "anime-identity.json"
DAY = 24 * 60 * 60.0
#: A answered identity (either way) is stable for a week; a resolution failure is
#: retried sooner because the missing side may simply have been down.
TTL_VERIFIED = 7 * DAY
TTL_OTHER = DAY
#: How many MAL search hits are examined in detail.  The first hit is often the
#: right one, and every extra hit is one more request against MAL's rate limit.
SEARCH_LIMIT = 5
#: At most one MAL request a second, the community-safe reading of an
#: undocumented limit (MAL can answer 403 rather than 429 when abused).
MAL_MIN_INTERVAL = 1.0
GRAPHQL_URL = "https://graphql.anilist.co"
USER_AGENT = "Lumora (personal desktop client)"

#: Which MAL media types can sit behind a TMDB kind.  A TMDB "tv" show is
#: usually MAL "tv", but ONA/OVA series are catalogued as shows there too; a
#: TMDB film is a MAL movie or special.  This constrains matching; it is not
#: evidence of anime anywhere.
_KIND_TYPES: dict[str, frozenset[str]] = {
    "tv": frozenset({"tv", "ona", "ova"}),
    "movie": frozenset({"movie", "special"}),
}

#: The AniList formats that fit each catalogue kind, in AniList's spelling.
_ANILIST_KINDS: dict[str, frozenset[str]] = {
    "tv": frozenset({"TV", "TV_SHORT", "ONA", "OVA"}),
    "movie": frozenset({"MOVIE", "SPECIAL"}),
}


class IdentityStatus(str, Enum):
    """The three answers callers must distinguish."""

    #: A real MAL anime entry matched, and an AniList id resolved safely.
    VERIFIED_ANIME = "verified_anime"
    #: No reliable MAL match, or MAL could not be asked.  Not "not anime".
    UNVERIFIED = "unverified"
    #: MAL verified the title, but its AniList id cannot be resolved safely.
    RESOLUTION_FAILURE = "resolution_failure"


class Reason:
    """Machine-readable ``reason`` values; frontends may show their own text."""

    MAL_ID = "mal-id"
    MAL_TITLE = "mal-title"
    NO_MAL_MATCH = "no-mal-match"
    AMBIGUOUS_MAL_MATCH = "ambiguous-mal-match"
    LOOKUP_UNAVAILABLE = "lookup-unavailable"
    ANILIST_MAL_LINK = "anilist-idmal"
    ANILIST_CROSSWALK = "anilist-crosswalk"
    ANILIST_MISSING = "anilist-missing"
    ANILIST_AMBIGUOUS = "anilist-ambiguous"
    ANILIST_UNVALIDATED = "anilist-unvalidated"
    ANILIST_LOOKUP_FAILED = "anilist-lookup-failed"
    #: AniList's own search named the title and its ``idMal`` link; MAL then
    #: confirmed the entry exists as anime.  MAL's title search had no answer.
    ANILIST_TITLE = "anilist-title"


class LookupUnavailable(Exception):
    """A lookup could not run, or its answer cannot be trusted.

    Raised by the lookup adapters for a missing Client ID, a network failure, a
    rate-limit refusal or a malformed payload.  :func:`identify` turns it into
    ``UNVERIFIED`` with ``lookup_failed=True`` (or, for AniList, into
    ``RESOLUTION_FAILURE``), so callers never see an exception for a lookup.
    """


@dataclass(frozen=True)
class MalMatch:
    """One MyAnimeList anime entry that the catalogue title was matched to."""

    mal_id: int
    title: str = ""
    media_type: str = ""
    start_date: str = ""
    episodes: int = 0
    alternative_titles: tuple[str, ...] = ()
    #: 1.0 for an exact title match; kept numeric so scoring can be added
    #: without changing the shape.  Nothing currently produces a partial score.
    score: float = 1.0


@dataclass(frozen=True)
class AniListTitle:
    """One AniList search hit: its id, its MAL link and the names it goes by."""

    anilist_id: int
    mal_id: int | None = None
    titles: tuple[str, ...] = ()
    year: int = 0
    format: str = ""


@dataclass(frozen=True)
class AniListResolution:
    """The outcome of resolving an AniList id for a verified MAL entry."""

    anilist_id: int | None = None
    #: ``"anilist-idmal"`` for AniList's own link, ``"anilist-crosswalk"`` for a
    #: crosswalk candidate that AniList's ``idMal`` then confirmed.
    source: str = ""
    #: True only when AniList itself confirmed the link back to the MAL id.
    validated: bool = False
    #: Candidate AniList ids when the answer is not safely single.
    candidates: tuple[int, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.anilist_id) and self.validated


@dataclass(frozen=True)
class AnimeIdentity:
    """The answer for one catalogue title.

    ``mal_id`` and ``anilist_id`` are different identifiers and are never
    interchangeable.  ``anilist_id`` is set only on ``VERIFIED_ANIME``.
    """

    status: IdentityStatus = IdentityStatus.UNVERIFIED
    mal_id: int | None = None
    anilist_id: int | None = None
    mal: MalMatch | None = None
    anilist: AniListResolution | None = None
    reason: str = ""
    #: MAL entries that matched equally well, when the identity is ambiguous.
    candidates: tuple[MalMatch, ...] = ()
    #: True when a lookup could not run at all (missing key, offline, refused).
    lookup_failed: bool = False

    @property
    def verified(self) -> bool:
        return self.status is IdentityStatus.VERIFIED_ANIME

    @property
    def resolution_failed(self) -> bool:
        return self.status is IdentityStatus.RESOLUTION_FAILURE

    @property
    def playable_anilist_id(self) -> int | None:
        """The id ani.pm may receive: an AniList id, never a MAL id."""
        return self.anilist_id if self.verified else None


class MalLookup(Protocol):
    """What :func:`identify` needs from MyAnimeList."""

    def search(self, title: str, *, limit: int = SEARCH_LIMIT) -> list[int]:
        """MAL ids whose title search matched *title*."""

    def details(self, mal_id: int) -> MalMatch:
        """One MAL entry; raises :class:`LookupUnavailable` when it cannot."""


class AniListLookup(Protocol):
    """What :func:`identify` needs from AniList's link fields."""

    def anilist_for_mal(self, mal_id: int) -> int | None:
        """The AniList id AniList itself links to *mal_id* (already validated)."""

    def mal_for_anilist(self, anilist_id: int) -> int | None:
        """The MAL id AniList records for *anilist_id* (for crosswalk checks)."""

    def search_titles(
        self, title: str, *, kind: str = "tv"
    ) -> Sequence[AniListTitle]:
        """AniList entries named *title*, most relevant first."""


# -- matching ---------------------------------------------------------------


def _normalise(text: object) -> str:
    """Casefolded, accent-free, punctuation-free, single-spaced words."""
    folded = unicodedata.normalize("NFKD", str(text or "")).casefold()
    kept = "".join(character if character.isalnum() else " " for character in folded)
    return " ".join(kept.split())


def _year(text: object) -> str:
    digits = "".join(character for character in str(text or "") if character.isdigit())
    return digits[:4] if len(digits) >= 4 else ""


def _positive(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def matches_catalogue(
    match: MalMatch, *, title: str, year: str = "", kind: str = "tv"
) -> bool:
    """Is *match* the same title as the catalogue entry?

    Exactness is the point: the normalised catalogue title must equal the MAL
    primary title or one of its alternative titles, the years must agree when
    both are known, and the MAL media type must fit the catalogue kind.  A
    near-miss ("Attack on Titan" vs "Attack on Titan Final Season") is not a
    match, because accepting it would be the title heuristic this module
    exists to remove.
    """
    wanted = _normalise(title)
    if not wanted:
        return False
    names = {_normalise(match.title)}
    names.update(_normalise(alias) for alias in match.alternative_titles)
    if wanted not in names:
        return False
    wanted_year = _year(year)
    match_year = _year(match.start_date)
    if wanted_year and match_year and wanted_year != match_year:
        return False
    media = " ".join(str(match.media_type or "").split()).lower()
    if not media:
        return True
    return media in _KIND_TYPES.get(str(kind).lower(), _KIND_TYPES["tv"])


def matches_anilist_title(
    entry: AniListTitle, *, title: str, year: str = "", kind: str = "tv"
) -> bool:
    """Is *entry* the same title as the catalogue row, under exactly that name?

    The same discipline as :func:`matches_catalogue`: the normalised catalogue
    title must equal one of AniList's own names, the years must agree when both
    are known, and the format must fit the catalogue kind.
    """
    wanted = _normalise(title)
    if not wanted:
        return False
    if wanted not in {_normalise(name) for name in entry.titles}:
        return False
    wanted_year = _year(year)
    if wanted_year and entry.year and wanted_year != str(entry.year):
        return False
    shape = " ".join(str(entry.format or "").split()).upper()
    if not shape:
        return True
    return shape in _ANILIST_KINDS.get(str(kind).lower(), _ANILIST_KINDS["tv"])


def search_anilist_title(
    title: str, *, kind: str = "tv", year: str = "",
    anilist: AniListLookup | None = None,
) -> tuple[AniListTitle | None, tuple[AniListTitle, ...]]:
    """AniList's own answer for *title*: one exact entry, several, or none.

    Only entries AniList links to a MAL id are considered, because an id with
    no MAL link is not something MAL can confirm.  Anything but a single exact
    entry is reported as several (or none), never picked between.
    """
    lookup = anilist if anilist is not None else AniListGraphQLLookup()
    search = getattr(lookup, "search_titles", None)
    if not callable(search):
        return None, ()
    try:
        rows = list(search(title, kind=kind))
    except LookupUnavailable:
        return None, ()
    exact = {
        entry.anilist_id: entry
        for entry in rows
        if isinstance(entry, AniListTitle)
        and entry.mal_id is not None
        and matches_anilist_title(entry, title=title, year=year, kind=kind)
    }
    if len(exact) != 1:
        return None, tuple(exact.values())
    return next(iter(exact.values())), ()


def anilist_link_for_mal(
    mal_id: int, *, title: str = "", kind: str = "tv",
    anilist: AniListLookup | None = None,
) -> int | None:
    """The one AniList entry whose own ``idMal`` is *mal_id*, found by name.

    The last resort when AniList's ``Media(idMal:)`` route and the crosswalk
    both came up empty: the search result must still carry this exact MAL link
    and there must be exactly one such entry.
    """
    number = _positive(mal_id)
    text = " ".join(str(title or "").split())
    if number is None or not text:
        return None
    lookup = anilist if anilist is not None else AniListGraphQLLookup()
    search = getattr(lookup, "search_titles", None)
    if not callable(search):
        return None
    try:
        rows = list(search(text, kind=kind))
    except LookupUnavailable:
        return None
    linked = {
        entry.anilist_id
        for entry in rows
        if isinstance(entry, AniListTitle) and entry.mal_id == number
    }
    return next(iter(linked)) if len(linked) == 1 else None


def _verified_through_anilist(
    mal: MalLookup, entry: AniListTitle, *, title: str, kind: str,
) -> AnimeIdentity | None:
    """MAL confirms the entry AniList named; then it is verified anime.

    AniList supplied the name match (it named this title exactly) and its own
    ``idMal`` link; MAL still has to answer for that id and agree that it is an
    anime of the catalogue's kind.  A missing entry or a type MAL does not call
    anime is no verification at all.
    """
    if entry.mal_id is None:
        return None
    try:
        details = mal.details(entry.mal_id)
    except LookupUnavailable:
        return None
    media = " ".join(str(details.media_type or "").split()).lower()
    if media and media not in _KIND_TYPES.get(str(kind).lower(), _KIND_TYPES["tv"]):
        return None
    return AnimeIdentity(
        status=IdentityStatus.VERIFIED_ANIME,
        mal_id=details.mal_id,
        anilist_id=entry.anilist_id,
        mal=details,
        anilist=AniListResolution(
            anilist_id=entry.anilist_id, source=Reason.ANILIST_TITLE,
            validated=True, reason=Reason.ANILIST_TITLE,
        ),
        reason=Reason.ANILIST_TITLE,
    )


# -- MyAnimeList -----------------------------------------------------------


class MalApiLookup:
    """The official MAL API, throttled, with the Client ID never hardcoded."""

    def __init__(
        self,
        client: object | None = None,
        *,
        settings: object | None = None,
        min_interval: float = MAL_MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._settings = settings
        self._min_interval = max(0.0, float(min_interval))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_request = 0.0

    # -- plumbing -----------------------------------------------------------
    def client(self) -> object:
        """The MAL client, built on first use from the configured Client ID."""
        if self._client is not None:
            return self._client
        client_id = mal_api.load_client_id(settings=self._settings)
        if not client_id:
            raise LookupUnavailable(mal_api.missing_client_id_message())
        self._client = mal_api.MalClient(client_id)
        return self._client

    def _wait_turn(self) -> None:
        """Keep at most one request per ``min_interval`` seconds."""
        if self._min_interval <= 0:
            return
        with self._lock:
            wait = self._last_request + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last_request = self._clock()

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        client = self.client()
        method = getattr(client, name, None)
        if not callable(method):
            raise LookupUnavailable(f"The MAL client has no {name}().")
        self._wait_turn()
        try:
            return method(*args, **kwargs)
        except mal_api.MalError as exc:  # missing key, network, refused, bad body
            raise LookupUnavailable(str(exc)) from None
        except (OSError, ValueError, TypeError) as exc:
            raise LookupUnavailable(f"MyAnimeList lookup failed: {exc}") from None

    # -- MalLookup ----------------------------------------------------------
    def search(self, title: str, *, limit: int = SEARCH_LIMIT) -> list[int]:
        text = " ".join(str(title or "").split())
        if not text:
            return []
        found = self._call("search", text, limit=max(1, int(limit)))
        ids: list[int] = []
        for item in found or ():
            mal_id = _positive(getattr(item, "id", None))
            if mal_id is not None and mal_id not in ids:
                ids.append(mal_id)
        return ids

    def details(self, mal_id: int) -> MalMatch:
        number = _positive(mal_id)
        if number is None:
            raise LookupUnavailable("A MAL id must be a positive integer.")
        found = self._call("details", number)
        return _match_from_details(found, number)


def _match_from_details(details: object, fallback_id: int) -> MalMatch:
    """Translate :class:`lgl.mal.AnimeDetails` (or a stub) into a MalMatch."""
    mal_id = _positive(getattr(details, "id", None)) or fallback_id
    title = " ".join(str(getattr(details, "title", "") or "").split())
    aliases = getattr(details, "alternative_titles", ()) or ()
    return MalMatch(
        mal_id=mal_id,
        title=title,
        media_type=str(getattr(details, "media_type", "") or "").strip().lower(),
        start_date=str(getattr(details, "start_date", "") or "").strip(),
        episodes=int(getattr(details, "episodes", 0) or 0),
        alternative_titles=tuple(
            " ".join(str(alias).split()) for alias in aliases if str(alias).strip()
        ),
    )


# -- AniList ----------------------------------------------------------------


class AniListGraphQLLookup:
    """AniList's own MAL links (``Media.idMal``), no key required."""

    def __init__(
        self,
        post: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._post = post
        self.timeout = float(timeout)

    def _query(self, query: str, variables: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._post is not None:
            try:
                return self._post(query, variables) or {}
            except LookupUnavailable:
                raise
            except (OSError, ValueError, TypeError) as exc:
                raise LookupUnavailable(f"AniList lookup failed: {exc}") from None
        body = json.dumps({"query": query, "variables": dict(variables)}).encode("utf-8")
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise LookupUnavailable(f"AniList lookup failed: {exc}") from None
        if not isinstance(payload, Mapping):
            raise LookupUnavailable("AniList returned an unexpected document.")
        return payload

    @staticmethod
    def _media(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return None
        media = data.get("Media")
        return media if isinstance(media, Mapping) else None

    def anilist_for_mal(self, mal_id: int) -> int | None:
        number = _positive(mal_id)
        if number is None:
            return None
        payload = self._query(
            "query ($malId: Int) {"
            " Media(type: ANIME, idMal: $malId) { id idMal }"
            "}",
            {"malId": number},
        )
        media = self._media(payload)
        if media is None:
            return None
        # AniList can answer with a different entry; only its own link counts.
        if _positive(media.get("idMal")) != number:
            return None
        return _positive(media.get("id"))

    def mal_for_anilist(self, anilist_id: int) -> int | None:
        number = _positive(anilist_id)
        if number is None:
            return None
        payload = self._query(
            "query ($id: Int) { Media(type: ANIME, id: $id) { id idMal } }",
            {"id": number},
        )
        media = self._media(payload)
        if media is None or _positive(media.get("id")) != number:
            return None
        return _positive(media.get("idMal"))

    SEARCH_QUERY = (
        "query ($search: String) {"
        " Page(perPage: 10) {"
        " media(search: $search, type: ANIME, sort: SEARCH_MATCH) {"
        " id idMal seasonYear format synonyms title { romaji english native }"
        " } } }"
    )

    def search_titles(
        self, title: str, *, kind: str = "tv"
    ) -> list[AniListTitle]:
        """AniList's catalogue search: the entries whose names match *title*."""
        text = " ".join(str(title or "").split())
        if not text:
            return []
        payload = self._query(self.SEARCH_QUERY, {"search": text})
        data = payload.get("data")
        page = data.get("Page") if isinstance(data, Mapping) else None
        rows = page.get("media") if isinstance(page, Mapping) else None
        found: list[AniListTitle] = []
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            number = _positive(row.get("id"))
            if number is None:
                continue
            names = row.get("title")
            names = names if isinstance(names, Mapping) else {}
            titles = [
                str(names.get(key) or "") for key in ("romaji", "english", "native")
            ]
            titles += [str(name or "") for name in row.get("synonyms") or ()]
            found.append(
                AniListTitle(
                    anilist_id=number,
                    mal_id=_positive(row.get("idMal")),
                    titles=tuple(name for name in titles if name),
                    year=int(_year(row.get("seasonYear")) or 0),
                    format=str(row.get("format") or ""),
                )
            )
        return found


# -- crosswalk --------------------------------------------------------------


def crosswalk_anilist_ids(
    mal_id: int,
    *,
    mapping: object | None = None,
    cache_path: Path | str | None = None,
) -> tuple[int, ...]:
    """AniList ids a crosswalk puts beside *mal_id* - candidates, not proof.

    The bundled Fribb/anime-lists crosswalk is the only crosswalk used.  Its
    records are never trusted on their own: :func:`resolve_anilist` checks every
    candidate against AniList's own ``idMal`` before it may be played.
    """
    number = _positive(mal_id)
    if number is None:
        return ()
    records: Sequence[Mapping[str, Any]] = ()
    if mapping is not None:
        records = tuple(getattr(mapping, "records", ()) or ())
    else:
        from . import anime_map  # local import: keeps this module import-light

        try:
            records = anime_map.load_mapping(cache_path=cache_path).records
        except (OSError, ValueError):
            return ()
    found: list[int] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if _positive(record.get("mal_id")) != number:
            continue
        anilist_id = _positive(record.get("anilist_id"))
        if anilist_id is not None and anilist_id not in found:
            found.append(anilist_id)
    return tuple(found)


# -- AniList resolution -----------------------------------------------------


def resolve_anilist(
    mal_id: int,
    *,
    anilist: AniListLookup | None = None,
    crosswalk: Callable[[int], Sequence[int]] | None = None,
) -> AniListResolution:
    """Resolve the AniList id of a MAL entry, or say why it cannot be.

    AniList's own ``idMal`` link is tried first.  Only then is the crosswalk
    consulted, and each of its candidates must be confirmed by AniList before
    it is used.  Every non-answer is structured: no id is ever guessed, and the
    MAL id is never returned as if it were an AniList id.
    """
    number = _positive(mal_id)
    if number is None:
        return AniListResolution(reason=Reason.ANILIST_MISSING)
    lookup = anilist if anilist is not None else AniListGraphQLLookup()
    try:
        linked = lookup.anilist_for_mal(number)
    except LookupUnavailable as exc:
        linked = None
        link_error = str(exc)
    else:
        link_error = ""
    if linked is not None:
        return AniListResolution(
            anilist_id=linked, source=Reason.ANILIST_MAL_LINK, validated=True,
            reason=Reason.ANILIST_MAL_LINK,
        )

    find = crosswalk if crosswalk is not None else crosswalk_anilist_ids
    try:
        candidates = tuple(
            candidate
            for candidate in (_positive(value) for value in find(number))
            if candidate is not None and candidate != number
        )
    except LookupUnavailable:
        candidates = ()
    # A crosswalk that repeats the MAL id is not an AniList id.
    candidates = tuple(dict.fromkeys(candidates))
    if not candidates:
        reason = Reason.ANILIST_LOOKUP_FAILED if link_error else Reason.ANILIST_MISSING
        return AniListResolution(reason=reason, candidates=())
    if len(candidates) > 1:
        return AniListResolution(
            reason=Reason.ANILIST_AMBIGUOUS, candidates=candidates,
        )
    candidate = candidates[0]
    try:
        confirmed = lookup.mal_for_anilist(candidate)
    except LookupUnavailable:
        confirmed = None
    if confirmed == number:
        return AniListResolution(
            anilist_id=candidate, source=Reason.ANILIST_CROSSWALK, validated=True,
            reason=Reason.ANILIST_CROSSWALK, candidates=candidates,
        )
    return AniListResolution(
        reason=Reason.ANILIST_UNVALIDATED, candidates=candidates,
    )


# -- the identity -----------------------------------------------------------


def _mal_candidates(
    mal: MalLookup, *, title: str, year: str, kind: str
) -> tuple[MalMatch | None, tuple[MalMatch, ...], str]:
    """Match the catalogue title on MAL: one match, many, or a reason."""
    ids = mal.search(title, limit=SEARCH_LIMIT)
    matched: list[MalMatch] = []
    for mal_id in list(ids)[:SEARCH_LIMIT]:
        try:
            details = mal.details(mal_id)
        except LookupUnavailable:
            continue  # one bad entry must not hide a good one
        if matches_catalogue(details, title=title, year=year, kind=kind):
            matched.append(details)
    if not matched:
        return None, (), Reason.NO_MAL_MATCH
    if len(matched) > 1:
        return None, tuple(matched), Reason.AMBIGUOUS_MAL_MATCH
    return matched[0], (), Reason.MAL_TITLE


def identify(
    *,
    title: str,
    kind: str = "tv",
    year: str = "",
    mal_id: int | None = None,
    mal: MalLookup | None = None,
    anilist: AniListLookup | None = None,
    crosswalk: Callable[[int], Sequence[int]] | None = None,
    cache: IdentityCache | None = None,
    cache_key: str = "",
    settings: object | None = None,
    anilist_id: int | None = None,
    now: float | None = None,
) -> AnimeIdentity:
    """Decide a catalogue title's anime identity and its AniList id.

    Returns one of the three :class:`IdentityStatus` answers; lookup failures
    are answers too (``UNVERIFIED`` with ``lookup_failed=True``), never
    exceptions.  Pass ``mal_id`` when the caller already has one (e.g. a MAL
    tile); otherwise *title*, *year* and *kind* are matched on MAL.

    *settings* is the app's settings object (``lgl.settings``), used only to
    find the configured MAL Client ID through :func:`lgl.mal.load_client_id`;
    it never holds a hardcoded key.  Pass ``mal=``/``anilist=`` to replace the
    adapters entirely (tests, or a caller that already has a client).

    ``anilist_id`` is for a page that already has an AniList id but no MAL id
    (an AniList anime detail page).  MAL stays the authority: the id is used
    only to discover the MAL entry AniList itself links to, and that entry is
    then verified on MAL.  An AniList id alone therefore verifies nothing - an
    id with no MAL link, or an outage, is ``UNVERIFIED``.  When ``mal_id`` is
    given it wins and ``anilist_id`` is ignored.
    """
    store = cache if cache is not None else _default_cache()
    hint = _positive(mal_id)
    wanted_anilist = _positive(anilist_id) if hint is None else None
    if cache_key:
        key = cache_key
    elif hint is not None:
        key = identity_key(kind=kind, title="", mal_id=hint)
    elif wanted_anilist is not None:
        key = f"anilist:{wanted_anilist}"
    else:
        key = identity_key(kind=kind, title=title, year=year)
    moment = time.time() if now is None else float(now)
    if store is not None:
        cached = store.get(key, now=moment)
        if cached is not None:
            return cached

    if wanted_anilist is not None:
        resolver = anilist if anilist is not None else AniListGraphQLLookup()
        try:
            hint = resolver.mal_for_anilist(wanted_anilist)
        except LookupUnavailable:
            identity = AnimeIdentity(
                status=IdentityStatus.UNVERIFIED,
                reason=Reason.LOOKUP_UNAVAILABLE,
                lookup_failed=True,
            )
            _remember(store, key, identity, moment)
            return identity
        if hint is None:
            # AniList knows of no MAL entry for this id, so MAL cannot verify
            # it; an AniList id alone is not proof of anime here.
            identity = AnimeIdentity(
                status=IdentityStatus.UNVERIFIED, reason=Reason.NO_MAL_MATCH,
            )
            _remember(store, key, identity, moment)
            return identity

    lookup = mal if mal is not None else MalApiLookup(settings=settings)
    matched: MalMatch | None = None
    candidates: tuple[MalMatch, ...] = ()
    if hint is not None:
        reason = Reason.MAL_ID
        try:
            matched = lookup.details(hint)
        except LookupUnavailable:
            identity = AnimeIdentity(
                status=IdentityStatus.UNVERIFIED,
                reason=Reason.LOOKUP_UNAVAILABLE,
                lookup_failed=True,
            )
            _remember(store, key, identity, moment)
            return identity
    else:
        try:
            matched, candidates, reason = _mal_candidates(
                lookup, title=title, year=year, kind=kind
            )
        except LookupUnavailable:
            identity = AnimeIdentity(
                status=IdentityStatus.UNVERIFIED,
                reason=Reason.LOOKUP_UNAVAILABLE,
                lookup_failed=True,
            )
            _remember(store, key, identity, moment)
            return identity

    if matched is None:
        # MAL's own title search gave no single answer.  AniList can still name
        # the entry and its ``idMal`` link, which MAL then has to confirm - so
        # a title MAL cannot find by name is not silently "not anime".
        entry, _others = search_anilist_title(
            title, kind=kind, year=year, anilist=anilist,
        )
        if entry is not None:
            rescued = _verified_through_anilist(lookup, entry, title=title, kind=kind)
            if rescued is not None:
                _remember(store, key, rescued, moment)
                return rescued
        identity = AnimeIdentity(
            status=IdentityStatus.UNVERIFIED, reason=reason, candidates=candidates,
        )
        _remember(store, key, identity, moment)
        return identity

    if wanted_anilist is not None:
        # AniList itself reported this id's MAL link above, so the page's own
        # id is the validated AniList side - no extra guess, no title search.
        resolution = AniListResolution(
            anilist_id=wanted_anilist,
            source=Reason.ANILIST_MAL_LINK,
            validated=True,
            reason=Reason.ANILIST_MAL_LINK,
        )
    else:
        resolution = resolve_anilist(
            matched.mal_id, anilist=anilist, crosswalk=crosswalk,
        )
        if not resolution.ok:
            # AniList could not be asked by MAL id and the crosswalk had no
            # answer; its search may still name the one entry carrying this link.
            linked = anilist_link_for_mal(
                matched.mal_id, title=matched.title, kind=kind, anilist=anilist,
            )
            if linked is not None:
                resolution = AniListResolution(
                    anilist_id=linked, source=Reason.ANILIST_TITLE,
                    validated=True, reason=Reason.ANILIST_TITLE,
                )
    if resolution.ok:
        identity = AnimeIdentity(
            status=IdentityStatus.VERIFIED_ANIME,
            mal_id=matched.mal_id,
            anilist_id=resolution.anilist_id,
            mal=matched,
            anilist=resolution,
            reason=resolution.reason,
        )
    else:
        identity = AnimeIdentity(
            status=IdentityStatus.RESOLUTION_FAILURE,
            mal_id=matched.mal_id,
            mal=matched,
            anilist=resolution,
            reason=resolution.reason,
        )
    _remember(store, key, identity, moment)
    return identity


def identity_key(*, kind: str, title: str, year: str = "", mal_id: int | None = None) -> str:
    """A stable cache key for one catalogue entry."""
    hint = _positive(mal_id)
    if hint is not None:
        return f"{str(kind).lower()}:mal:{hint}"
    return f"{str(kind).lower()}:{_normalise(title)}:{_year(year)}"


def identify_tmdb(
    tmdb_kind: str,
    tmdb_id: int,
    *,
    title: str,
    year: str = "",
    **kwargs: Any,
) -> AnimeIdentity:
    """Identify a TMDB entry, keyed by its TMDB id."""
    number = _positive(tmdb_id)
    kind = "tv" if str(tmdb_kind).lower() == "tv" else "movie"
    key = f"{kind}:tmdb:{number}" if number is not None else ""
    return identify(title=title, kind=kind, year=year, cache_key=key, **kwargs)


# -- cache ------------------------------------------------------------------


def _identity_record(identity: AnimeIdentity, expires: float) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": identity.status.value,
        "mal_id": identity.mal_id,
        "anilist_id": identity.anilist_id,
        "reason": identity.reason,
        "expires": expires,
    }
    if identity.mal is not None:
        data["mal"] = {
            "mal_id": identity.mal.mal_id,
            "title": identity.mal.title,
            "media_type": identity.mal.media_type,
            "start_date": identity.mal.start_date,
            "episodes": identity.mal.episodes,
            "alternative_titles": list(identity.mal.alternative_titles),
        }
    if identity.anilist is not None:
        data["anilist"] = {
            "anilist_id": identity.anilist.anilist_id,
            "source": identity.anilist.source,
            "validated": identity.anilist.validated,
            "candidates": list(identity.anilist.candidates),
            "reason": identity.anilist.reason,
        }
    if identity.candidates:
        data["candidates"] = [
            {"mal_id": item.mal_id, "title": item.title}
            for item in identity.candidates
        ]
    return data


def _match_record(raw: object) -> MalMatch | None:
    if not isinstance(raw, Mapping):
        return None
    number = _positive(raw.get("mal_id"))
    if number is None:
        return None
    return MalMatch(
        mal_id=number,
        title=str(raw.get("title", "") or ""),
        media_type=str(raw.get("media_type", "") or ""),
        start_date=str(raw.get("start_date", "") or ""),
        episodes=int(raw.get("episodes", 0) or 0),
        alternative_titles=tuple(
            str(alias) for alias in (raw.get("alternative_titles") or ())
        ),
    )


def _identity_from_record(raw: Mapping[str, Any]) -> AnimeIdentity | None:
    try:
        status = IdentityStatus(str(raw.get("status")))
    except ValueError:
        return None
    anilist_raw = raw.get("anilist")
    resolution = None
    if isinstance(anilist_raw, Mapping):
        resolution = AniListResolution(
            anilist_id=_positive(anilist_raw.get("anilist_id")),
            source=str(anilist_raw.get("source", "") or ""),
            validated=bool(anilist_raw.get("validated")),
            candidates=tuple(
                candidate
                for candidate in (
                    _positive(value) for value in (anilist_raw.get("candidates") or ())
                )
                if candidate is not None
            ),
            reason=str(anilist_raw.get("reason", "") or ""),
        )
    candidates = tuple(
        match
        for match in (_match_record(item) for item in (raw.get("candidates") or ()))
        if match is not None
    )
    return AnimeIdentity(
        status=status,
        mal_id=_positive(raw.get("mal_id")),
        anilist_id=_positive(raw.get("anilist_id")),
        mal=_match_record(raw.get("mal")),
        anilist=resolution,
        reason=str(raw.get("reason", "") or ""),
        candidates=candidates,
    )




class IdentityCache:
    """A small in-memory cache, optionally mirrored to one JSON file.

    Verified answers are kept for :data:`TTL_VERIFIED`; unverified and
    resolution-failure answers for :data:`TTL_OTHER`; a lookup that could not
    run at all is **never** kept, so a missing Client ID or an offline moment is
    retried on the next play instead of being remembered as an answer.
    """

    def __init__(self, path: Path | str | None = None, *, memory_limit: int = 512) -> None:
        self.path = Path(path) if path is not None else None
        self._memory: dict[str, tuple[AnimeIdentity, float]] = {}
        self._limit = max(1, int(memory_limit))
        self._lock = threading.Lock()

    # -- disk ---------------------------------------------------------------
    def _read_disk(self) -> dict[str, Any]:
        if self.path is None:
            return {}
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_disk(self, entries: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(dict(entries), indent=0, sort_keys=True), "utf-8")
            temporary.replace(self.path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass

    # -- public -------------------------------------------------------------
    def get(self, key: str, *, now: float | None = None) -> AnimeIdentity | None:
        if not key:
            return None
        moment = time.time() if now is None else float(now)
        with self._lock:
            hit = self._memory.get(key)
            if hit is not None:
                identity, expires = hit
                if expires > moment:
                    return identity
                self._memory.pop(key, None)
            entries = self._read_disk()
            raw = entries.get(key)
            if not isinstance(raw, Mapping):
                return None
            try:
                expires = float(raw.get("expires", 0) or 0)
            except (TypeError, ValueError):
                expires = 0.0
            if expires <= moment:
                return None
            identity = _identity_from_record(raw)
            if identity is None:
                return None
            self._memory[key] = (identity, expires)
            return identity

    def put(self, key: str, identity: AnimeIdentity, *,
            now: float | None = None) -> None:
        if not key or identity.lookup_failed:
            return
        moment = time.time() if now is None else float(now)
        ttl = (
            TTL_VERIFIED
            if identity.status is IdentityStatus.VERIFIED_ANIME
            else TTL_OTHER
        )
        expires = moment + ttl
        with self._lock:
            self._memory[key] = (identity, expires)
            while len(self._memory) > self._limit:
                self._memory.pop(next(iter(self._memory)))
            if self.path is not None:
                entries = self._read_disk()
                entries[key] = _identity_record(identity, expires)
                self._write_disk(entries)

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            if self.path is not None:
                self._write_disk({})

    def __len__(self) -> int:
        with self._lock:
            return len(self._memory)


def _remember(
    store: IdentityCache | None, key: str, identity: AnimeIdentity, moment: float
) -> None:
    if store is not None:
        store.put(key, identity, now=moment)


_DEFAULT_CACHE: IdentityCache | None = None
_DEFAULT_LOCK = threading.Lock()


def _default_cache() -> IdentityCache:
    """The process-wide cache both frontends share."""
    global _DEFAULT_CACHE
    with _DEFAULT_LOCK:
        if _DEFAULT_CACHE is None:
            _DEFAULT_CACHE = IdentityCache(
                platform_support.cache_dir() / CACHE_FILENAME
            )
        return _DEFAULT_CACHE


def default_mal_lookup(settings: object | None = None) -> MalApiLookup:
    """The MAL lookup a frontend should use, with its own settings if it has them."""
    return MalApiLookup(settings=settings)


def default_anilist_lookup() -> AniListGraphQLLookup:
    """The AniList lookup (no key, public GraphQL)."""
    return AniListGraphQLLookup()
