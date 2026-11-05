"""TMDB reads: configuration, searching, details, seasons, poster URLs.

The API key is never written in source, never logged, and never put in an error
message: it can come from ``TMDB_API_KEY`` in the environment, from a local
``.env`` next to the project, or from the settings store (Settings → Providers),
in that order.  This module only ever *reads* configuration - it will not
create or overwrite a ``.env`` you already have.

Every endpoint used here is TMDB's documented v3 API.  The client takes an
injectable ``fetch(path, params)`` so tests never touch the network and never
need a real key.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
#: Only these hosts are ever contacted for metadata and images.
TMDB_HOSTS = ("api.themoviedb.org",)
IMAGE_HOSTS = ("image.tmdb.org",)
USER_AGENT = "Lumora (personal desktop client)"
DEFAULT_TIMEOUT = 20.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_KEY_LENGTH = 256
#: v3 keys are 32 hex characters; v4 read tokens are long bearer-safe strings.
_KEY_RE = re.compile(r"^[A-Za-z0-9._\-]{8,256}$")
_KEY_QUERY = re.compile(r"api_key=([^&\s]+)")
_BEARER = re.compile(r"(Bearer\s+)[^\s,;\"']+", re.IGNORECASE)
_REDACTED = "api_key=***"
DOTENV_NAME = ".env"


class TmdbError(Exception):
    """A TMDB read failed, in a way the UI can explain.

    ``kind`` is ``"missing-key"``, ``"auth"``, ``"rate-limited"``,
    ``"not-found"``, ``"server"``, ``"network"`` or ``"invalid-response"``.
    Messages never contain the API key.
    """

    def __init__(self, message: str, *, kind: str = "network", status: int | None = None) -> None:
        super().__init__(_redact(message))
        self.kind = kind
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate-limited", "server", "network")


def _redact(text: str) -> str:
    """Strip any ``api_key=`` or ``Bearer`` value that reached a string."""
    value = _KEY_QUERY.sub(_REDACTED, str(text))
    return _BEARER.sub(r"\1***", value)


def is_bearer_token(value: Any) -> bool:
    """True for a TMDB v4 Read Access Token (a JWT), false for a v3 key.

    A v4 token is a three-part JWT (``header.payload.signature``); a v3 key is
    a 32-character hex string.  The distinction matters because TMDB accepts
    only one of them in the ``api_key`` query parameter.
    """
    text = clean_api_key(value)
    return bool(text) and text.count(".") == 2 and len(text) > 40


def auth_query(api_key: Any) -> dict[str, str]:
    """The ``api_key`` query parameter for a v3 key, or ``{}`` for a v4 token."""
    text = clean_api_key(api_key)
    return {} if is_bearer_token(text) else {"api_key": text}


def auth_headers(api_key: Any) -> dict[str, str]:
    """The ``Authorization`` header for a v4 token, or ``{}`` for a v3 key."""
    text = clean_api_key(api_key)
    return {"Authorization": f"Bearer {text}"} if is_bearer_token(text) else {}


def clean_api_key(value: Any) -> str:
    """A usable key, or ``""``.  Whitespace and odd characters are rejected."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > MAX_KEY_LENGTH:
        return ""
    return text if _KEY_RE.match(text) else ""


def project_dotenv() -> Path:
    """The ``.env`` beside the package, which is where the README points."""
    return Path(__file__).resolve().parent.parent / DOTENV_NAME


def read_dotenv(path: Path | str) -> dict[str, str]:
    """Read a minimal ``KEY=VALUE`` file.  Missing or unreadable -> ``{}``."""
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        if not name:
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def load_api_key(
    *,
    settings: Any = None,
    environ: Mapping[str, str] | None = None,
    dotenv_paths: Sequence[Path | str] | None = None,
) -> str:
    """The configured TMDB key: environment, then ``.env``, then settings."""
    env = os.environ if environ is None else environ
    key = clean_api_key(env.get("TMDB_API_KEY", ""))
    if key:
        return key
    paths = list(dotenv_paths) if dotenv_paths is not None else [project_dotenv()]
    for path in paths:
        key = clean_api_key(read_dotenv(path).get("TMDB_API_KEY", ""))
        if key:
            return key
    if settings is not None:
        try:
            key = clean_api_key(settings.api_key("tmdb"))
        except (AttributeError, TypeError, ValueError):
            key = ""
    return key


def missing_key_message(settings_path: Path | str | None = None) -> str:
    """Where to put the key, without pretending there is a default value."""
    where = f" or add it to your settings file ({settings_path})" if settings_path else ""
    return (
        "No TMDB API key is configured. Put TMDB_API_KEY=your_key_here in a "
        f".env file next to the project{where}, or set it in Settings → Providers. "
        "Nothing is downloaded and no key is stored by this app until you do."
    )


def poster_url(path: Any, *, size: str = "w500") -> str:
    """``/abc.jpg`` -> an https image URL; anything unexpected -> ``""``."""
    return _image_url(path, size)


def backdrop_url(path: Any, *, size: str = "w780") -> str:
    return _image_url(path, size)


def _image_url(path: Any, size: str) -> str:
    text = str(path or "").strip()
    if not text.startswith("/") or len(text) > 260:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._\-/]+", text[1:]):
        return ""
    return f"{IMAGE_BASE}/{size}{text}"


def _error_detail(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw[:4096].decode("utf-8", "replace"))
    except ValueError:
        return ""
    if isinstance(payload, dict):
        for name in ("status_message", "message", "error"):
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:200]
    return ""


def _http_json(path: str, params: Mapping[str, Any], *, api_key: str, timeout: float) -> Any:
    query = {str(name): str(value) for name, value in params.items() if value is not None}
    # A v3 key travels in the query string; a v4 Read Access Token travels in
    # the Authorization header.  They are not interchangeable.
    query.update(auth_query(api_key))
    url = f"{API_BASE}{path}?" + urllib.parse.urlencode(query)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    headers.update(auth_headers(api_key))
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
        }.get(status, "server" if status >= 500 else "http")
        raise TmdbError(
            f"TMDB answered HTTP {status}" + (f": {detail}" if detail else "."),
            kind=kind,
            status=status,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TmdbError(f"Could not reach TMDB: {getattr(exc, 'reason', exc)}") from None
    if len(body) > MAX_BODY_BYTES:
        raise TmdbError("TMDB returned more data than expected.", kind="invalid-response")
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise TmdbError("TMDB returned invalid JSON.", kind="invalid-response") from None


@dataclass(frozen=True)
class MediaItem:
    """One row in a grid: a movie or a show, without the full detail."""

    kind: str  # "movie" | "tv"
    tmdb_id: int
    title: str
    year: str = ""
    poster_url: str = ""
    backdrop_url: str = ""
    overview: str = ""
    rating: float | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.tmdb_id}"


@dataclass(frozen=True)
class Episode:
    season: int
    number: int
    title: str
    air_date: str = ""
    overview: str = ""
    still_url: str = ""


@dataclass(frozen=True)
class Season:
    number: int
    name: str = ""
    episode_count: int = 0
    air_date: str = ""
    episodes: tuple[Episode, ...] = ()


@dataclass(frozen=True)
class MediaDetails:
    kind: str
    tmdb_id: int
    title: str
    year: str = ""
    poster_url: str = ""
    backdrop_url: str = ""
    overview: str = ""
    genres: tuple[str, ...] = ()
    runtime: int | None = None
    rating: float | None = None
    release_date: str = ""
    seasons: tuple[Season, ...] = ()
    tagline: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.tmdb_id}"

    @property
    def runtime_label(self) -> str:
        if not self.runtime:
            return ""
        hours, minutes = divmod(int(self.runtime), 60)
        return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def _year(value: Any) -> str:
    text = str(value or "").strip()
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


def _rating(payload: Mapping[str, Any]) -> float | None:
    value = payload.get("vote_average")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(number, 1) if number > 0 else None


def _genres(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("genres")
    names: list[str] = []
    if isinstance(raw, Sequence):
        for entry in raw:
            if isinstance(entry, Mapping):
                name = " ".join(str(entry.get("name") or "").split())
                if name:
                    names.append(name)
    return tuple(names[:8])


class TmdbClient:
    """The TMDB reads this app needs, with the key kept on the object only."""

    def __init__(
        self,
        api_key: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        language: str = "en-US",
        fetch: Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self._key = clean_api_key(api_key)
        self.timeout = float(timeout)
        self.language = language
        self._fetch = fetch

    @property
    def configured(self) -> bool:
        return bool(self._key)

    # -- transport ----------------------------------------------------------

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self._key:
            raise TmdbError(missing_key_message(), kind="missing-key")
        query: dict[str, Any] = {"language": self.language}
        query.update(params or {})
        try:
            if self._fetch is not None:
                data = self._fetch(path, query)
            else:
                data = _http_json(path, query, api_key=self._key, timeout=self.timeout)
        except TmdbError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise TmdbError(f"TMDB could not be read: {_redact(str(exc))}") from None
        if not isinstance(data, dict):
            raise TmdbError("TMDB returned an unexpected document.", kind="invalid-response")
        return data

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """A raw TMDB read for callers that need an endpoint this class lacks."""
        return self._get(path, params)

    def test_connection(self) -> str:
        """Verify the API key works by calling a lightweight endpoint.
        Returns a human-readable success message, or raises TmdbError.
        """
        # Use /configuration - lightweight, documented, doesn't require search
        data = self._get("/configuration")
        if not isinstance(data, dict) or "images" not in data:
            raise TmdbError("TMDB answered but response lacks expected configuration data.", kind="invalid-response")
        return "TMDB connection succeeded; image configuration retrieved."

    # -- reads --------------------------------------------------------------

    def search(self, query: str, *, page: int = 1) -> list[MediaItem]:
        """Movies and shows matching *query*; empty for a blank query."""
        text = " ".join(str(query or "").split())
        if not text:
            return []
        data = self._get(
            "/search/multi",
            {"query": text[:200], "include_adult": "false", "page": max(1, int(page))},
        )
        return self._items(data.get("results"), default_kind="")

    def trending(self, kind: str = "movie", *, window: str = "week") -> list[MediaItem]:
        chosen = "tv" if str(kind).lower() == "tv" else "movie"
        span = "day" if str(window).lower() == "day" else "week"
        data = self._get(f"/trending/{chosen}/{span}")
        return self._items(data.get("results"), default_kind=chosen)

    def popular(self, kind: str = "movie", *, page: int = 1) -> list[MediaItem]:
        chosen = "tv" if str(kind).lower() == "tv" else "movie"
        data = self._get(f"/{chosen}/popular", {"page": max(1, int(page))})
        return self._items(data.get("results"), default_kind=chosen)

    def details(self, kind: str, tmdb_id: int) -> MediaDetails:
        chosen = "tv" if str(kind).lower() == "tv" else "movie"
        data = self._get(f"/{chosen}/{int(tmdb_id)}")
        return self._details(data, chosen, int(tmdb_id))

    def season(self, tmdb_id: int, season_number: int) -> Season:
        number = int(season_number)
        if number < 0:
            raise ValueError("A season number cannot be negative.")
        data = self._get(f"/tv/{int(tmdb_id)}/season/{number}")
        return self._season(data, number)

    # -- normalisation ------------------------------------------------------

    @staticmethod
    def _items(raw: Any, *, default_kind: str) -> list[MediaItem]:
        items: list[MediaItem] = []
        if not isinstance(raw, Sequence):
            return items
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            kind = str(entry.get("media_type") or default_kind or "").lower()
            if kind not in ("movie", "tv"):
                continue  # people and companies are not titles
            raw_id = entry.get("id")
            if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id <= 0:
                continue
            title = " ".join(str(entry.get("title") or entry.get("name") or "").split())
            if not title:
                continue
            items.append(
                MediaItem(
                    kind=kind,
                    tmdb_id=raw_id,
                    title=title[:300],
                    year=_year(entry.get("release_date") or entry.get("first_air_date")),
                    poster_url=poster_url(entry.get("poster_path")),
                    backdrop_url=backdrop_url(entry.get("backdrop_path")),
                    overview=" ".join(str(entry.get("overview") or "").split())[:1200],
                    rating=_rating(entry),
                )
            )
        return items

    @staticmethod
    def _details(data: Mapping[str, Any], kind: str, tmdb_id: int) -> MediaDetails:
        title = " ".join(str(data.get("title") or data.get("name") or "").split())
        runtime: int | None = None
        if kind == "movie":
            value = data.get("runtime")
            runtime = int(value) if isinstance(value, int) and value > 0 else None
        else:
            lengths = data.get("episode_run_time")
            if isinstance(lengths, Sequence):
                for value in lengths:
                    if isinstance(value, int) and value > 0:
                        runtime = value
                        break
        seasons: list[Season] = []
        if kind == "tv":
            raw_seasons = data.get("seasons")
            if isinstance(raw_seasons, Sequence):
                for entry in raw_seasons:
                    if not isinstance(entry, Mapping):
                        continue
                    number = entry.get("season_number")
                    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                        continue
                    seasons.append(
                        Season(
                            number=number,
                            name=" ".join(str(entry.get("name") or "").split()),
                            episode_count=int(entry.get("episode_count") or 0),
                            air_date=str(entry.get("air_date") or ""),
                        )
                    )
        return MediaDetails(
            kind=kind,
            tmdb_id=tmdb_id,
            title=title or f"{kind.title()} {tmdb_id}",
            year=_year(data.get("release_date") or data.get("first_air_date")),
            poster_url=poster_url(data.get("poster_path")),
            backdrop_url=backdrop_url(data.get("backdrop_path")),
            overview=" ".join(str(data.get("overview") or "").split())[:4000],
            genres=_genres(data),
            runtime=runtime,
            rating=_rating(data),
            release_date=str(data.get("release_date") or data.get("first_air_date") or ""),
            seasons=tuple(seasons),
            tagline=" ".join(str(data.get("tagline") or "").split())[:300],
        )

    @staticmethod
    def _season(data: Mapping[str, Any], number: int) -> Season:
        episodes: list[Episode] = []
        raw = data.get("episodes")
        if isinstance(raw, Sequence):
            for entry in raw:
                if not isinstance(entry, Mapping):
                    continue
                episode_number = entry.get("episode_number")
                if not isinstance(episode_number, int) or isinstance(episode_number, bool):
                    continue
                if episode_number < 1:
                    continue
                episodes.append(
                    Episode(
                        season=int(entry.get("season_number") or number),
                        number=episode_number,
                        title=" ".join(str(entry.get("name") or "").split())[:200]
                        or f"Episode {episode_number}",
                        air_date=str(entry.get("air_date") or ""),
                        overview=" ".join(str(entry.get("overview") or "").split())[:1200],
                        still_url=poster_url(entry.get("still_path"), size="w300"),
                    )
                )
        episodes.sort(key=lambda episode: episode.number)
        return Season(
            number=number,
            name=" ".join(str(data.get("name") or "").split()),
            episode_count=len(episodes) or int(data.get("episode_count") or 0),
            air_date=str(data.get("air_date") or ""),
            episodes=tuple(episodes),
        )


__all__ = [
    "API_BASE",
    "DEFAULT_TIMEOUT",
    "DOTENV_NAME",
    "IMAGE_BASE",
    "IMAGE_HOSTS",
    "TMDB_HOSTS",
    "Episode",
    "MediaDetails",
    "MediaItem",
    "Season",
    "TmdbClient",
    "TmdbError",
    "auth_headers",
    "auth_query",
    "backdrop_url",
    "clean_api_key",
    "is_bearer_token",
    "load_api_key",
    "missing_key_message",
    "poster_url",
    "project_dotenv",
    "read_dotenv",
]
