"""ani.pm catalogue reads: search, title metadata, series and episodes.

ani.pm (https://ani.pm/developers) is an anime catalogue and player that keys
playback by AniList **or** MyAnimeList id and needs no API key for catalogue
requests.  This module is deliberately *metadata only*: it never scrapes
streams, never rehosts content and never builds a URL the provider did not
document.  Playback embed URLs come from the API when it returns one, and are
otherwise constructed from validated ids with the documented route shape
(see :func:`playback_url`).

Identifiers stay separate everywhere: ``AniTitle.anilist_id`` and
``AniTitle.mal_id`` are distinct fields and are never substituted for one
another.  An ani.pm page slug is neither an AniList nor a MAL id and is kept
only as opaque text.

The exact JSON layout can vary between API revisions, so every parser here is
tolerant: it accepts the documented field names plus common aliases, skips
malformed rows instead of failing the whole page, and never invents values.
Network access is injectable (``fetch=``) so the test suite never touches the
live service.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from . import streaming

API_BASE = "https://ani.pm/api/partner/v1"
USER_AGENT = "native-game-library (personal desktop client)"
DEFAULT_TIMEOUT = 20.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_QUERY_LENGTH = 200
MAX_SEARCH_LIMIT = 100
MAX_PAGE = 10_000
#: A cached catalogue answer is reused at most this long, even if the server
#: asked for more, so stale listings cannot live forever in memory.
MAX_CACHE_SECONDS = 600.0

#: The four availability flags ani.pm publishes per title/episode.  ``sub`` and
#: ``dub`` can also be *constructed* from validated ids when the API returns no
#: embed URL (``/embed/ani/<anilist>/<episode>/<sub|dub>`` is the documented
#: route); the hard-sub variants only exist when the API hands us an embed URL,
#: so nothing is fabricated for them.
LANGUAGES: tuple[str, ...] = ("sub", "dub", "subHard", "dubHard")
CONSTRUCTABLE_LANGUAGES: frozenset[str] = frozenset({"sub", "dub"})
LANGUAGE_LABELS: dict[str, str] = {
    "sub": "Sub",
    "dub": "Dub",
    "subHard": "Sub·Hard",
    "dubHard": "Dub·Hard",
}

_YEAR = re.compile(r"\b(\d{4})\b")
_MAX_AGE = re.compile(r"max-age=(\d+)", re.IGNORECASE)

Fetch = Callable[[str, Mapping[str, Any], Mapping[str, str]], Any]


class AniPmError(Exception):
    """An ani.pm catalogue read failed, in a way the UI can explain.

    ``kind`` is ``"rate-limited"``, ``"not-found"``, ``"server"``, ``"network"``
    or ``"invalid-response"``.  ani.pm needs no key, so there is no auth kind.
    """

    def __init__(self, message: str, *, kind: str = "network",
                 status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate-limited", "server", "network")


# -- data --------------------------------------------------------------------


@dataclass(frozen=True)
class AniTitle:
    """One catalogue row or title page from ani.pm.

    ``anilist_id`` is the primary key ani.pm documents for its AniList route;
    ``mal_id`` is kept separately and may be 0 when the API does not map one.
    ``slug`` is an opaque page slug - never an id.
    """

    anilist_id: int
    title: str
    mal_id: int = 0
    poster_url: str = ""
    year: str = ""
    format: str = ""
    score: float | None = None
    genres: tuple[str, ...] = ()
    adult: bool = False
    status: str = ""
    episodes: int = 0
    synopsis: str = ""
    slug: str = ""

    @property
    def key(self) -> str:
        return f"anipm:{self.anilist_id}"

    @property
    def kind(self) -> str:
        return "anime"

    @property
    def id(self) -> int:
        """The AniList id, under the attribute the shared tiles read."""
        return self.anilist_id

    @property
    def rating(self) -> float | None:
        return self.score


@dataclass(frozen=True)
class Availability:
    """Which language tracks exist for a title or one episode."""

    sub: bool = False
    dub: bool = False
    sub_hard: bool = False
    dub_hard: bool = False

    def allows(self, language: object) -> bool:
        return {
            "sub": self.sub,
            "dub": self.dub,
            "subHard": self.sub_hard,
            "dubHard": self.dub_hard,
        }.get(_language_key(language), False)

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(name for name in LANGUAGES if self.allows(name))

    @property
    def empty(self) -> bool:
        return not any(self.languages)


@dataclass(frozen=True)
class AniEpisode:
    """One episode, with the embed URLs the API actually returned.

    ``embeds`` maps a language key (from :data:`LANGUAGES`) to a validated
    https embed URL on the pinned ani.pm origin; ``default_embed`` is a
    language-less ``embed`` string the API returned for the episode.  Embeds
    that failed origin validation were dropped at parse time.
    """

    number: int
    title: str = ""
    embeds: Mapping[str, str] = field(default_factory=dict)
    default_embed: str = ""

    def embed_url_for(self, language: object) -> str:
        """The API's embed URL for this episode and track, or ``""``.

        Raises ``EmbedUrlError`` when the language is not a known track.
        """
        key = _language_key(language)
        if key is None:
            raise streaming.EmbedUrlError(
                f"ani.pm has no track called {language!r}; "
                f"available tracks: {', '.join(LANGUAGES)}."
            )
        return str(self.embeds.get(key, "") or self.default_embed or "")

    def playable_languages(self, available: Availability | None = None) -> tuple[str, ...]:
        """Tracks this episode can actually be played with.

        A track is playable when the API returned an embed for it, or when the
        title's availability allows it *and* the documented route can be
        constructed from ids (sub/dub only).  Hard-sub tracks are never
        constructed - nothing is invented for them.
        """
        allowed = available or Availability()
        playable: list[str] = []
        for name in LANGUAGES:
            if self.embeds.get(name) or self.default_embed:
                if allowed.allows(name) or name in self.embeds:
                    playable.append(name)
            elif allowed.allows(name) and name in CONSTRUCTABLE_LANGUAGES:
                playable.append(name)
        return tuple(playable)


@dataclass(frozen=True)
class AniSeries:
    """/series/{anilistId}: a title plus its availability and episodes."""

    anilist_id: int
    title: str = ""
    poster_url: str = ""
    year: str = ""
    format: str = ""
    score: float | None = None
    genres: tuple[str, ...] = ()
    adult: bool = False
    synopsis: str = ""
    episodes: tuple[AniEpisode, ...] = ()
    available: Availability = field(default_factory=Availability)


# -- normalisation helpers ---------------------------------------------------


def language_key(value: object) -> str | None:
    """The canonical track key (``sub``/``dub``/``subHard``/``dubHard``).

    Returns ``None`` for anything that is not a known ani.pm track, so
    callers never pass an invented language onward.
    """
    return _language_key(value)


def _language_key(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.replace("_", "").replace("-", "").replace(" ", "").lower()
    aliases = {
        "sub": "sub", "subbed": "sub", "raw": "sub",
        "dub": "dub", "dubbed": "dub",
        "subhard": "subHard", "hardsub": "subHard",
        "dubhard": "dubHard", "harddub": "dubHard",
    }
    return aliases.get(lowered)


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else 0
    return 0


def _year(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value) if 1900 <= value <= 2999 else ""
    return (_YEAR.search(str(value or "")) or ["", ""])[1] if value else ""


def _score(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # Some payloads score 0-100 (averageScore), others 0-10.
    if number > 10:
        number = number / 10.0
    return round(number, 1) if number <= 10 else None


def _text(value: object, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _synopsis(value: object) -> str:
    return " ".join(str(value or "").split())[:2000]


def _genres(value: object) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for entry in value:
            name = _text(entry.get("name") if isinstance(entry, Mapping) else entry, 60)
            if name and name not in names:
                names.append(name)
    return tuple(names[:10])


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "adult", "18+")
    return False


def _poster(value: object) -> str:
    """A poster image URL; ``""`` for anything that is not an https URL."""
    candidates: list[object] = []
    if isinstance(value, Mapping):
        for name in ("large", "medium", "url", "original"):
            candidates.append(value.get(name))
    else:
        candidates.append(value)
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or len(text) > 1000:
            continue
        try:
            split = urllib.parse.urlsplit(text)
        except ValueError:
            continue
        if split.scheme == "https" and split.netloc and not split.username:
            return text
    return ""


def _dig(raw: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return None


def _title_text(value: object) -> str:
    """``title`` as a string, whether the API sent a string or a name map."""
    if isinstance(value, Mapping):
        # English first for this UI; romaji/native remain the fallbacks.
        for name in ("english", "display", "default", "romaji", "native"):
            text = _text(value.get(name))
            if text:
                return text
        return ""
    return _text(value)


_ROW_KEYS = ("anilistId", "anilist_id", "id", "number", "episode")


def _looks_like_row(value: Mapping[str, Any]) -> bool:
    return any(name in value for name in _ROW_KEYS)


def _unwrap(payload: Any, keys: Sequence[str]) -> list[Any]:
    """Rows from a bare list or from the first matching wrapper key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for name in keys:
            value = payload.get(name)
            if isinstance(value, list):
                return value
            if isinstance(value, Mapping):
                # A single wrapped row: {"data": {"anilistId": 5, ...}}.
                if _looks_like_row(value):
                    return [value]
                # An id-keyed map: {"titles": {"1": {...}, "2": {...}}}.
                return [
                    entry for entry in value.values()
                    if isinstance(entry, (Mapping, list))
                ]
        return [payload]
    return []


def _parse_title(raw: Any) -> AniTitle | None:
    """One ``AniTitle`` from a row, or ``None`` for a malformed row."""
    if not isinstance(raw, Mapping):
        return None
    # Tolerate a {"node": {...}} or {"title": {...}} envelope like MAL's.
    for name in ("node", "media", "result"):
        inner = raw.get(name)
        if isinstance(inner, Mapping):
            raw = inner
            break
    ids = raw.get("ids")
    anilist_id = _as_int(_dig(raw, "anilistId", "anilist_id", "anilistID", "anilist"))
    if not anilist_id and isinstance(ids, Mapping):
        anilist_id = _as_int(_dig(ids, "anilistId", "anilist_id", "anilist"))
    if not anilist_id:
        # /titles rows identify the title by its AniList id; a plain "id" is
        # the documented row key.  A slug is never accepted as an id.
        anilist_id = _as_int(raw.get("id"))
    if not anilist_id:
        return None
    mal_source = _dig(raw, "malId", "mal_id", "malID", "myanimelistId", "mal")
    if mal_source is None and isinstance(ids, Mapping):
        mal_source = _dig(ids, "malId", "mal_id", "mal")
    mal_id = _as_int(mal_source.get("id") if isinstance(mal_source, Mapping) else mal_source)
    title = _title_text(_dig(raw, "title", "name"))
    if not title:
        title = f"Anime {anilist_id}"
    return AniTitle(
        anilist_id=anilist_id,
        title=title,
        mal_id=mal_id,
        poster_url=_poster(_dig(raw, "poster", "cover", "image", "mainPicture")),
        year=_year(_dig(raw, "year", "startDate", "start_date", "airedFrom", "seasonYear")),
        format=_text(_dig(raw, "format", "type", "mediaType", "kind"), 24).upper(),
        score=_score(_dig(raw, "score", "rating", "averageScore", "mean")),
        genres=_genres(raw.get("genres")),
        adult=_boolean(_dig(raw, "adult", "isAdult", "is_adult", "nsfw", "hentai")),
        status=_text(_dig(raw, "status"), 40),
        episodes=_as_int(_dig(raw, "episodes", "episodeCount", "totalEpisodes", "count")),
        synopsis=_synopsis(_dig(raw, "synopsis", "description", "overview")),
        slug=_text(raw.get("slug"), 200),
    )


def _parse_episode(raw: Any) -> AniEpisode | None:
    """One ``AniEpisode`` from a row, or ``None`` for a malformed row."""
    if not isinstance(raw, Mapping):
        return None
    number = _as_int(_dig(raw, "number", "episode", "episodeNumber", "ep", "index"))
    if not number:
        return None
    embeds: dict[str, str] = {}
    default_embed = ""
    embed_source = _dig(raw, "embed", "embedUrl", "embed_url", "player", "url")
    if isinstance(embed_source, Mapping):
        for key, value in embed_source.items():
            language = _language_key(key)
            url = _validated_embed(value)
            if language and url:
                embeds[language] = url
    else:
        default_embed = _validated_embed(embed_source)
    return AniEpisode(
        number=number,
        title=_text(_dig(raw, "title", "name"), 200),
        embeds=embeds,
        default_embed=default_embed,
    )


def _validated_embed(value: object) -> str:
    """The embed URL when it passes the pinned ani.pm origin check, else ``""``.

    Untrusted API content never reaches the player unvalidated: anything that
    is not an https ani.pm ``/embed`` URL is dropped here.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return streaming.validate_embed_url(streaming.ANI_PROVIDER, text)
    except streaming.EmbedUrlError:
        return ""


def _availability(raw: Any) -> Availability:
    """``available.sub|dub|subHard|dubHard`` with tolerant aliases."""
    if not isinstance(raw, Mapping):
        return Availability()
    def flag(*names: str) -> bool:
        for name in names:
            if name in raw:
                return _boolean(raw[name])
        return False
    return Availability(
        sub=flag("sub", "subbed"),
        dub=flag("dub", "dubbed"),
        sub_hard=flag("subHard", "sub_hard", "hardsub"),
        dub_hard=flag("dubHard", "dub_hard", "harddub"),
    )


# -- transport ---------------------------------------------------------------


def _request_url(path: str, params: Mapping[str, Any] | None = None) -> str:
    """The exact request URL, with the query percent-encoded."""
    query = {
        str(name): str(value)
        for name, value in (params or {}).items()
        if value is not None
    }
    url = f"{API_BASE}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def _max_age(response: Any) -> float:
    """The response's ``Cache-Control: max-age`` in seconds, or ``0``."""
    try:
        header = response.headers.get("Cache-Control", "")
    except AttributeError:
        return 0.0
    match = _MAX_AGE.search(str(header or ""))
    if not match:
        return 0.0
    return min(float(match.group(1)), MAX_CACHE_SECONDS)


def _error_detail(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw[:4096].decode("utf-8", "replace"))
    except ValueError:
        return ""
    if isinstance(payload, Mapping):
        for name in ("message", "error", "status_message"):
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                return _text(value, 200)
    return ""


def _http_json(url: str, *, timeout: float) -> tuple[Any, float]:
    """GET one URL; returns ``(decoded payload, cache seconds)``."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            max_age = _max_age(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = _error_detail(exc.read(4096))
        except OSError:
            detail = ""
        status = int(exc.code)
        kind = {404: "not-found", 429: "rate-limited"}.get(status, "server")
        raise AniPmError(
            f"ani.pm answered HTTP {status}" + (f": {detail}" if detail else "."),
            kind=kind,
            status=status,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AniPmError(
            f"Could not reach ani.pm: {getattr(exc, 'reason', exc)}",
            kind="network",
        ) from None
    if len(body) > MAX_BODY_BYTES:
        raise AniPmError(
            "ani.pm returned more data than expected.", kind="invalid-response"
        )
    try:
        return json.loads(body.decode("utf-8", "replace")), max_age
    except ValueError:
        raise AniPmError(
            "ani.pm returned invalid JSON.", kind="invalid-response"
        ) from None


# -- client ------------------------------------------------------------------


class AniPmClient:
    """The ani.pm catalogue reads this app needs.  No key, no account."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        fetch: Fetch | None = None,
    ) -> None:
        self.timeout = float(timeout)
        self._fetch = fetch
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- transport ----------------------------------------------------------

    def _get(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        url = _request_url(path, params)
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached is not None and cached[0] > now:
            return cached[1]
        if self._fetch is not None:
            data: Any = self._fetch(path, dict(params or {}),
                                     {"Accept": "application/json",
                                      "User-Agent": USER_AGENT})
            max_age = 0.0
            if isinstance(data, tuple) and len(data) == 2:
                data, max_age = data
        else:
            data, max_age = _http_json(url, timeout=self.timeout)
        if not isinstance(data, (Mapping, list)):
            raise AniPmError(
                "ani.pm returned an unexpected document.", kind="invalid-response"
            )
        if max_age > 0:
            self._cache[url] = (now + min(max_age, MAX_CACHE_SECONDS), data)
        return data

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- reads --------------------------------------------------------------

    def search(self, query: object, *, page: object = 1,
               limit: object = 24) -> list[AniTitle]:
        """Titles matching *query*, one catalogue page at a time.

        Blank queries return an empty list without a request.  ``page`` and
        ``limit`` are clamped to the documented bounds rather than rejected,
        so a stored setting cannot produce an invalid request.
        """
        text = " ".join(str(query or "").split())
        if not text:
            return []
        params = {
            "q": text[:MAX_QUERY_LENGTH],
            "page": _clamp(1 if page is None else page, 1, MAX_PAGE),
            "limit": _clamp(24 if limit is None else limit, 1, MAX_SEARCH_LIMIT),
        }
        payload = self._get("/titles", params)
        return _items(payload, ("data", "results", "titles", "items"))

    def title(self, anilist_id: object) -> AniTitle:
        """One title by its **AniList** id: ``GET /titles/{anilistId}``."""
        identifier = _positive(anilist_id, "anilist_id")
        payload = self._get(f"/titles/{identifier}")
        title = _first_title(payload)
        if title is None:
            raise AniPmError(
                f"ani.pm has no title with AniList id {identifier}.",
                kind="not-found",
            )
        return title

    def title_by_mal(self, mal_id: object) -> AniTitle:
        """One title by its **MyAnimeList** id: ``GET /titles/mal-{malId}``.

        The MAL id stays a MAL id: this route is the only place it is used,
        and the returned title keeps both ids in their own fields.
        """
        identifier = _positive(mal_id, "mal_id")
        payload = self._get(f"/titles/mal-{identifier}")
        title = _first_title(payload)
        if title is None:
            raise AniPmError(
                f"ani.pm has no title with MAL id {identifier}.", kind="not-found"
            )
        return title

    def series(self, anilist_id: object) -> AniSeries:
        """Series data plus episode availability: ``GET /series/{anilistId}``."""
        identifier = _positive(anilist_id, "anilist_id")
        payload = self._get(f"/series/{identifier}")
        if not isinstance(payload, Mapping):
            raise AniPmError(
                "ani.pm returned an unexpected series document.",
                kind="invalid-response",
            )
        raw = payload
        inner = _dig(payload, "series", "data")
        if isinstance(inner, Mapping):
            raw = inner
        base = _parse_title(raw)
        if base is None:
            raise AniPmError(
                f"ani.pm returned no series for AniList id {identifier}.",
                kind="not-found",
            )
        available = _availability(_dig(raw, "available", "availability", "languages"))
        episodes = tuple(_episodes(raw.get("episodes")))
        # Episodes that carry their own embeds prove a track exists even when
        # the series-level flags are missing.
        if available.empty and episodes:
            available = Availability(
                sub=any("sub" in episode.embeds or episode.default_embed
                        for episode in episodes),
                dub=any("dub" in episode.embeds for episode in episodes),
                sub_hard=any("subHard" in episode.embeds for episode in episodes),
                dub_hard=any("dubHard" in episode.embeds for episode in episodes),
            )
        return AniSeries(
            anilist_id=base.anilist_id,
            title=base.title,
            poster_url=base.poster_url,
            year=base.year,
            format=base.format,
            score=base.score,
            genres=base.genres,
            adult=base.adult,
            synopsis=base.synopsis,
            episodes=episodes,
            available=available,
        )

    def episodes(self, anilist_id: object) -> list[AniEpisode]:
        """Episodes only: ``GET /titles/{anilistId}/episodes``."""
        identifier = _positive(anilist_id, "anilist_id")
        payload = self._get(f"/titles/{identifier}/episodes")
        return _episodes(payload)

    def lookup(
        self,
        *,
        anilist: object = None,
        mal: object = None,
    ) -> list[AniTitle]:
        """Map known ids: ``GET /lookup?anilist={ids}&mal={ids}``.

        Both parameters may be one id or a sequence of ids.  The response rows
        keep AniList and MAL ids in separate fields, so a hit found by MAL id
        still carries its AniList id (and vice versa) for playback routing.
        """
        params: dict[str, Any] = {}
        anilist_ids = _id_list(anilist)
        mal_ids = _id_list(mal)
        if anilist_ids:
            params["anilist"] = ",".join(str(number) for number in anilist_ids)
        if mal_ids:
            params["mal"] = ",".join(str(number) for number in mal_ids)
        if not params:
            return []
        payload = self._get("/lookup", params)
        return _items(payload, ("data", "results", "titles", "items"))

    def recent(self, *, limit: object = 24) -> list[AniTitle]:
        """Optional discovery: ``GET /recent``."""
        payload = self._get("/recent", {"limit": _clamp(limit, 1, MAX_SEARCH_LIMIT)})
        return _items(payload, ("data", "results", "titles", "items"))

    def schedule(self) -> list[AniTitle]:
        """Optional discovery: ``GET /schedule``."""
        payload = self._get("/schedule")
        return _items(payload, ("data", "results", "schedule", "days", "items"))

    def top(self, *, limit: object = 24) -> list[AniTitle]:
        """Optional discovery: ``GET /top``."""
        payload = self._get("/top", {"limit": _clamp(limit, 1, MAX_SEARCH_LIMIT)})
        return _items(payload, ("data", "results", "titles", "items"))


# -- playback ----------------------------------------------------------------


def playback_url(
    anilist_id: object,
    episode: object,
    language: object,
    api_embed: object = None,
) -> str:
    """The ani.pm embed URL for one episode and track.

    The API's own ``embed`` URL wins when one was returned and passes the
    origin check.  Otherwise the documented route is constructed from the
    **AniList** id, the episode number and a supported language - only for
    ``sub`` and ``dub``, because ani.pm documents no hard-sub route to
    construct.  Anything else raises instead of guessing.
    """
    identifier = _positive(anilist_id, "anilist_id")
    number = _positive(episode, "episode")
    text = str(api_embed or "").strip()
    if text:
        return streaming.validate_embed_url(streaming.ANI_PROVIDER, text)
    key = _language_key(language)
    if key is None or key not in CONSTRUCTABLE_LANGUAGES:
        raise streaming.EmbedUrlError(
            f"ani.pm gave no embed URL and no route is documented for "
            f"{LANGUAGE_LABELS.get(str(language), str(language or 'that track'))}; "
            "only Sub and Dub can be built from ids."
        )
    return streaming.ani_url(identifier, number, language=key)


def _positive(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive number.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError(f"{label} must be a positive number.")
    if number <= 0:
        raise ValueError(f"{label} must be a positive number.")
    return number


def _clamp(value: object, low: int, high: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _id_list(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        source = value
    else:
        source = [value]
    ids: list[int] = []
    for entry in source:
        number = _as_int(entry)
        if number and number not in ids:
            ids.append(number)
    return ids


def _items(payload: Any, wrapper_keys: Sequence[str]) -> list[AniTitle]:
    titles: list[AniTitle] = []
    seen: set[int] = set()
    for row in _unwrap(payload, wrapper_keys):
        # A wrapper row may itself hold the list under one of the keys.
        if isinstance(row, Mapping):
            inner = _unwrap(row, wrapper_keys)
            if inner and inner != [row]:
                for entry in inner:
                    title = _parse_title(
                        entry.get("node") if isinstance(entry, Mapping) and
                        isinstance(entry.get("node"), Mapping) else entry
                    )
                    if title is not None and title.anilist_id not in seen:
                        seen.add(title.anilist_id)
                        titles.append(title)
                continue
        title = _parse_title(row)
        if title is not None and title.anilist_id not in seen:
            seen.add(title.anilist_id)
            titles.append(title)
    return titles


def _first_title(payload: Any) -> AniTitle | None:
    titles = _items(payload, ("data", "title", "results"))
    return titles[0] if titles else None


def _episodes(payload: Any) -> list[AniEpisode]:
    rows = _unwrap(payload, ("episodes", "data", "results", "items"))
    episodes: list[AniEpisode] = []
    seen: set[int] = set()
    for row in rows:
        if isinstance(row, Mapping) and not any(
            name in row for name in ("number", "episode", "episodeNumber", "ep", "index")
        ):
            inner = _unwrap(row, ("episodes", "data", "results"))
            if inner and inner != [row]:
                for entry in inner:
                    episode = _parse_episode(entry)
                    if episode is not None and episode.number not in seen:
                        seen.add(episode.number)
                        episodes.append(episode)
                continue
        episode = _parse_episode(row)
        if episode is not None and episode.number not in seen:
            seen.add(episode.number)
            episodes.append(episode)
    episodes.sort(key=lambda item: item.number)
    return episodes


__all__ = [
    "API_BASE",
    "CONSTRUCTABLE_LANGUAGES",
    "DEFAULT_TIMEOUT",
    "LANGUAGE_LABELS",
    "LANGUAGES",
    "MAX_BODY_BYTES",
    "MAX_SEARCH_LIMIT",
    "AniEpisode",
    "AniPmClient",
    "AniPmError",
    "AniSeries",
    "AniTitle",
    "Availability",
    "language_key",
    "playback_url",
]
