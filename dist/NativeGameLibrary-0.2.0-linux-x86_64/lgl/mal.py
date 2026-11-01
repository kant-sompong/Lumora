"""MyAnimeList reads: searching, details, seasons and rankings.

This client talks to the public MyAnimeList API v2 with a *Client ID* only.
The Client ID is sent exclusively in the ``X-MAL-CLIENT-ID`` request header; it
is never a query parameter, never logged and never written into an error
message.  No OAuth flow, client secret or redirect URI is involved: OAuth would
only be needed for user-scoped data (lists, scores), which this module does not
implement.  It is metadata only - nothing is ever written to MyAnimeList.

The Client ID can come from ``MAL_CLIENT_ID`` in the environment or from the
settings store (Settings → Providers), in that order.

Every endpoint used here is part of MyAnimeList's documented v2 API.  The
client takes an injectable ``fetch(path, params, headers)`` so tests never touch
the network and never need a real Client ID.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

API_BASE = "https://api.myanimelist.net/v2"
#: Image hosts MAL serves artwork from.
IMAGE_HOSTS = ("cdn.myanimelist.net", "myanimelist.net")
CLIENT_ID_HEADER = "X-MAL-CLIENT-ID"
USER_AGENT = "native-game-library (personal desktop client)"
DEFAULT_TIMEOUT = 20.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_CLIENT_ID_LENGTH = 256
#: Public read endpoints accept a Client ID and nothing else.
MAX_SEARCH_LIMIT = 100
MAX_LIST_LIMIT = 500
#: Fields requested for every anime, so list and detail payloads agree.
LIST_FIELDS = (
    "id,title,synopsis,mean,num_episodes,status,genres,start_date,"
    "main_picture,alternative_titles,num_scoring_users,media_type,rating,"
    "average_episode_duration"
)
SEASONS = ("winter", "spring", "summer", "fall")
SEASON_SORTS = ("anime_score", "anime_num_list_users")
RANKING_TYPES = (
    "all",
    "airing",
    "upcoming",
    "tv",
    "ova",
    "movie",
    "special",
    "bypopularity",
    "favorite",
)
#: Client IDs are printable, whitespace-free tokens.  Length is generous but
#: bounded so a pasted paragraph is rejected rather than sent.
_CLIENT_ID_RE = re.compile(r"^[!-~]{4,256}$")
#: Anything that looks like the header or a query value is redacted.
_HEADER_VALUE = re.compile(
    r"(X-MAL-CLIENT-ID\s*[:=]\s*)([^\s,;\"']+)", re.IGNORECASE
)
_CLIENT_QUERY = re.compile(r"((?:client_id|X-MAL-CLIENT-ID)=)([^&\s]+)", re.IGNORECASE)
_BBCODE = re.compile(r"\[/?[A-Za-z][^\]]{0,120}\]")
_YEAR = re.compile(r"\b(\d{4})\b")

Fetch = Callable[[str, Mapping[str, Any], Mapping[str, str]], Any]


class MalError(Exception):
    """A MyAnimeList read failed, in a way the UI can explain.

    ``kind`` is ``"missing-key"``, ``"auth"``, ``"rate-limited"``,
    ``"not-found"``, ``"server"``, ``"network"`` or ``"invalid-response"``.
    Messages never contain the Client ID.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "network",
        status: int | None = None,
        client_id: str = "",
    ) -> None:
        super().__init__(_redact(message, client_id))
        self.kind = kind
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate-limited", "server", "network")


def _redact(text: str, client_id: str = "") -> str:
    """Remove the Client ID and any header/query value that reached a string."""
    value = str(text)
    if client_id:
        value = value.replace(client_id, "***")
    value = _HEADER_VALUE.sub(r"\1***", value)
    return _CLIENT_QUERY.sub(r"\1***", value)


def clean_client_id(value: Any) -> str:
    """A usable Client ID, or ``""``.  Whitespace and odd characters are rejected."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > MAX_CLIENT_ID_LENGTH:
        return ""
    return text if _CLIENT_ID_RE.match(text) else ""


def auth_headers(client_id: Any) -> dict[str, str]:
    """The header that carries the Client ID; empty when there is no key."""
    text = clean_client_id(client_id)
    return {CLIENT_ID_HEADER: text} if text else {}


def load_client_id(
    *,
    settings: Any = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """The configured Client ID: environment first, then the settings store."""
    env = os.environ if environ is None else environ
    key = clean_client_id(env.get("MAL_CLIENT_ID", ""))
    if key:
        return key
    if settings is not None:
        try:
            key = clean_client_id(settings.api_key("mal"))
        except (AttributeError, TypeError, ValueError):
            key = ""
    return key


def missing_client_id_message() -> str:
    """Where to put the Client ID, without pretending there is a default."""
    return (
        "No MyAnimeList Client ID is configured. Set MAL_CLIENT_ID=your_client_id "
        "in the environment, or add it in Settings → Providers. Only the public "
        "anime catalogue is read; no account is needed."
    )


def poster_url(main_picture: Any) -> str:
    """``main_picture`` -> an https image URL; anything unexpected -> ``""``."""
    if not isinstance(main_picture, Mapping):
        return ""
    for name in ("large", "medium"):
        text = str(main_picture.get(name) or "").strip()
        if not text or len(text) > 500:
            continue
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme == "https" and parsed.netloc:
            return text
    return ""


def _year(start_date: Any) -> str:
    text = str(start_date or "").strip()
    match = _YEAR.search(text)
    return match.group(1) if match else ""


def _synopsis(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    text = _BBCODE.sub(" ", text)
    return " ".join(text.split())[:limit]


def _rating(payload: Mapping[str, Any]) -> float | None:
    value = payload.get("mean")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number, 1) if number > 0 else None


def _episodes(payload: Mapping[str, Any]) -> int:
    value = payload.get("num_episodes")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _status(payload: Mapping[str, Any]) -> str:
    text = str(payload.get("status") or "").strip().lower()
    return text if text in ("finished_airing", "currently_airing", "not_yet_aired") else ""


def _media_type(payload: Mapping[str, Any]) -> str:
    text = str(payload.get("media_type") or "").strip().lower()
    return text if re.fullmatch(r"[a-z_]{1,20}", text) else ""


def _genres(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("genres")
    names: list[str] = []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            if isinstance(entry, Mapping):
                name = " ".join(str(entry.get("name") or "").split())
                if name:
                    names.append(name[:60])
    return tuple(names[:8])


def _alternative_titles(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("alternative_titles")
    names: list[str] = []
    if isinstance(raw, Mapping):
        for name in ("en", "ja"):
            text = " ".join(str(raw.get(name) or "").split())
            if text:
                names.append(text[:200])
        synonyms = raw.get("synonyms")
        if isinstance(synonyms, Sequence) and not isinstance(synonyms, (str, bytes)):
            for entry in synonyms:
                text = " ".join(str(entry or "").split())
                if text:
                    names.append(text[:200])
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return tuple(unique[:8])


def _item(node: Any) -> "AnimeItem | None":
    """One ``AnimeItem`` from a node, or ``None`` for a malformed node."""
    if not isinstance(node, Mapping):
        return None
    raw_id = node.get("id")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id <= 0:
        return None
    title = " ".join(str(node.get("title") or "").split())
    if not title:
        return None
    return AnimeItem(
        id=raw_id,
        title=title[:300],
        year=_year(node.get("start_date")),
        synopsis=_synopsis(node.get("synopsis"), limit=1200),
        poster_url=poster_url(node.get("main_picture")),
        rating=_rating(node),
        episodes=_episodes(node),
        status=_status(node),
        media_type=_media_type(node),
    )


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


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
                return " ".join(value.split())[:200]
    return ""


def _http_json(
    path: str,
    params: Mapping[str, Any],
    *,
    client_id: str,
    timeout: float,
) -> Any:
    query = {str(name): str(value) for name, value in params.items() if value is not None}
    # The Client ID never travels in the query string - only in the header.
    url = f"{API_BASE}{path}?" + urllib.parse.urlencode(query)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    headers.update(auth_headers(client_id))
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = _error_detail(exc.read(4096))
        except OSError:
            detail = ""
        status = int(exc.code)
        kind = {
            401: "auth",
            403: "auth",
            404: "not-found",
            429: "rate-limited",
        }.get(status, "server")
        if status == 400 and "client id" in detail.lower():
            # MAL reports an unknown/mistyped Client ID as HTTP 400
            # ``{"error": "bad_request", "message": "Invalid client id"}``,
            # not as 401.  Treat it as an auth problem with a useful hint.
            kind = "auth"
            raise MalError(
                "MyAnimeList rejected the Client ID (HTTP 400: "
                f"{detail}). Check the Client ID at myanimelist.net/apiconfig.",
                kind=kind,
                status=status,
                client_id=client_id,
            ) from None
        raise MalError(
            f"MyAnimeList answered HTTP {status}" + (f": {detail}" if detail else "."),
            kind=kind,
            status=status,
            client_id=client_id,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MalError(
            f"Could not reach MyAnimeList: {getattr(exc, 'reason', exc)}",
            kind="network",
            client_id=client_id,
        ) from None
    if len(body) > MAX_BODY_BYTES:
        raise MalError(
            "MyAnimeList returned more data than expected.",
            kind="invalid-response",
            client_id=client_id,
        )
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise MalError(
            "MyAnimeList returned invalid JSON.",
            kind="invalid-response",
            client_id=client_id,
        ) from None


@dataclass(frozen=True)
class AnimeItem:
    """One row in an anime grid, without the full detail."""

    id: int
    title: str
    year: str = ""
    synopsis: str = ""
    poster_url: str = ""
    rating: float | None = None
    episodes: int = 0
    status: str = ""
    media_type: str = ""

    @property
    def key(self) -> str:
        return f"anime:{self.id}"

    @property
    def kind(self) -> str:
        """The shared media-kind label used by poster tiles."""
        return "anime"


@dataclass(frozen=True)
class AnimeDetails:
    """Everything the detail view shows about one anime."""

    id: int
    title: str
    synopsis: str = ""
    year: str = ""
    poster_url: str = ""
    genres: tuple[str, ...] = ()
    rating: float | None = None
    episodes: int = 0
    status: str = ""
    media_type: str = ""
    start_date: str = ""
    alternative_titles: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"anime:{self.id}"

    @property
    def kind(self) -> str:
        return "anime"


class MalClient:
    """The MyAnimeList reads this app needs, with the Client ID kept private."""

    def __init__(
        self,
        client_id: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        fetch: Fetch | None = None,
    ) -> None:
        self._client_id = clean_client_id(client_id)
        self.timeout = float(timeout)
        self._fetch = fetch

    @property
    def configured(self) -> bool:
        return bool(self._client_id)

    # -- transport ----------------------------------------------------------

    def _get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if not self._client_id:
            raise MalError(missing_client_id_message(), kind="missing-key")
        query: dict[str, Any] = dict(params or {})
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        headers.update(auth_headers(self._client_id))
        try:
            if self._fetch is not None:
                data = self._fetch(path, query, headers)
            else:
                data = _http_json(
                    path, query, client_id=self._client_id, timeout=self.timeout
                )
        except MalError:
            raise
        except OSError as exc:
            raise MalError(
                f"Could not reach MyAnimeList: {_redact(str(exc), self._client_id)}",
                kind="network",
                client_id=self._client_id,
            ) from None
        except (ValueError, TypeError) as exc:
            raise MalError(
                f"MyAnimeList returned an unexpected document: "
                f"{_redact(str(exc), self._client_id)}",
                kind="invalid-response",
                client_id=self._client_id,
            ) from None
        if not isinstance(data, Mapping):
            raise MalError(
                "MyAnimeList returned an unexpected document.",
                kind="invalid-response",
                client_id=self._client_id,
            )
        return data

    def test_connection(self) -> str:
        """Verify the Client ID works with a lightweight read.

        Returns a human-readable success message, or raises ``MalError``.
        Uses the documented ranking endpoint with a non-empty parameter set;
        MAL rejects a bare/empty search as ``400 Invalid Parameters``.
        """
        data = self._get(
            "/anime/ranking",
            {"ranking_type": "bypopularity", "limit": 1, "fields": "id,title"},
        )
        if not isinstance(data.get("data"), list):
            raise MalError(
                "MyAnimeList answered but the response lacks expected data.",
                kind="invalid-response",
                client_id=self._client_id,
            )
        return "MyAnimeList connection succeeded; the API answered."

    # -- reads --------------------------------------------------------------

    def search(self, query: str, *, limit: int = 24) -> list[AnimeItem]:
        """Anime matching *query*; empty for a blank query."""
        text = " ".join(str(query or "").split())
        if not text:
            return []
        data = self._get(
            "/anime",
            {
                "q": text[:200],
                "limit": _clamp(limit, 1, MAX_SEARCH_LIMIT),
                "offset": 0,
                "fields": LIST_FIELDS,
            },
        )
        return self._items(data.get("data"))

    def details(self, anime_id: int) -> AnimeDetails:
        """One anime by id.  ``/anime/{id}`` returns a single object."""
        identifier = self._identifier(anime_id)
        data = self._get(f"/anime/{identifier}", {"fields": LIST_FIELDS})
        # The endpoint returns the anime object itself, not a ``{"data": ...}``
        # wrapper.  Accept a wrapper only as a tolerance for proxy responses.
        if "id" not in data and isinstance(data.get("data"), Mapping):
            data = data["data"]
        return self._details(data, identifier)

    def season(
        self,
        year: int,
        season: str,
        *,
        limit: int = 24,
        sort: str = "anime_score",
    ) -> list[AnimeItem]:
        """Anime from one broadcast season, best-scored first by default."""
        name = str(season or "").strip().lower()
        if name not in SEASONS:
            raise ValueError(f"Unknown season: {season!r}. Use one of {', '.join(SEASONS)}.")
        order = str(sort or "").strip()
        if order not in SEASON_SORTS:
            raise ValueError(
                f"Unknown season sort: {sort!r}. Use one of {', '.join(SEASON_SORTS)}."
            )
        try:
            number = int(year)
        except (TypeError, ValueError):
            raise ValueError("A season year must be a four-digit number.") from None
        if number < 1900 or number > 9999:
            raise ValueError("A season year must be a four-digit number.")
        data = self._get(
            f"/anime/season/{number}/{name}",
            {
                "sort": order,
                "limit": _clamp(limit, 1, MAX_LIST_LIMIT),
                "offset": 0,
                "fields": LIST_FIELDS,
            },
        )
        return self._items(data.get("data"))

    def ranking(
        self,
        ranking_type: str = "bypopularity",
        *,
        limit: int = 24,
    ) -> list[AnimeItem]:
        """The anime ranking for *ranking_type*."""
        kind = str(ranking_type or "").strip().lower()
        if kind not in RANKING_TYPES:
            raise ValueError(
                f"Unknown ranking type: {ranking_type!r}. "
                f"Use one of {', '.join(RANKING_TYPES)}."
            )
        data = self._get(
            "/anime/ranking",
            {
                "ranking_type": kind,
                "limit": _clamp(limit, 1, MAX_LIST_LIMIT),
                "offset": 0,
                "fields": LIST_FIELDS,
            },
        )
        return self._items(data.get("data"))

    # -- normalisation ------------------------------------------------------

    @staticmethod
    def _identifier(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("An anime id must be a positive integer.")
        return value

    @staticmethod
    def _items(raw: Any) -> list[AnimeItem]:
        items: list[AnimeItem] = []
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return items
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            item = _item(entry.get("node"))
            if item is not None:
                items.append(item)
        return items

    @staticmethod
    def _details(data: Mapping[str, Any], anime_id: int) -> AnimeDetails:
        title = " ".join(str(data.get("title") or "").split())
        start_date = str(data.get("start_date") or "").strip()
        return AnimeDetails(
            id=anime_id,
            title=title[:300] or f"Anime {anime_id}",
            synopsis=_synopsis(data.get("synopsis"), limit=4000),
            year=_year(start_date),
            poster_url=poster_url(data.get("main_picture")),
            genres=_genres(data),
            rating=_rating(data),
            episodes=_episodes(data),
            status=_status(data),
            media_type=_media_type(data),
            start_date=start_date[:40],
            alternative_titles=_alternative_titles(data),
        )


__all__ = [
    "API_BASE",
    "AnimeDetails",
    "AnimeItem",
    "CLIENT_ID_HEADER",
    "DEFAULT_TIMEOUT",
    "IMAGE_HOSTS",
    "LIST_FIELDS",
    "MalClient",
    "MalError",
    "MAX_BODY_BYTES",
    "RANKING_TYPES",
    "SEASONS",
    "SEASON_SORTS",
    "USER_AGENT",
    "auth_headers",
    "clean_client_id",
    "load_client_id",
    "missing_client_id_message",
    "poster_url",
]
