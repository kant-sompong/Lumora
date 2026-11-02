"""Verified client and parsers for the public Kryoto catalog API.

The interfaces used here were checked against live responses on 2026-10-30:

``GET /api/games?limit=N&offset=M``
    ``{"games": [<full record>, ...], "count": <records on this page>}``
    ``offset`` works; ``page`` is ignored by the server; ``count`` is the size
    of the returned page, **not** a catalog total.  A page is therefore
    complete only when it returns fewer records than requested.

``GET /api/games/search?q=...&limit=N``
    ``{"results": [<lightweight record>, ...]}``.  Results do not include
    download links.

``GET /api/games/{slug}``
    ``{"game": <full record>, "related": [...], "art": {...}}``

``GET /api/games/{slug}/downloads``
    Links, checksums and versioned releases for one game.

``GET /api/games/{slug}/mirrors``
    Host/state/progress of the site's own mirror-building jobs.  These are not
    download URLs and are never treated as such.

No documentation page exists (``/api/docs`` answers 404), so every shape above
is a live observation, not an invented contract.  This module only reads those
public endpoints: it never posts, never authenticates, and never follows a
source link.
"""

from __future__ import annotations

import copy
import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import __version__
from . import metadata as meta
from .util import AppError

KRYOTO_ORIGIN = "https://kryo.to"
KRYOTO_API = f"{KRYOTO_ORIGIN}/api"
KRYOTO_HOSTS = ("kryo.to",)
#: Artwork the API itself points at.  Approval still only permits HTTPS hosts.
KRYOTO_IMAGE_HOSTS = (
    "kryo.to",
    "shared.akamai.steamstatic.com",
    "cdn.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com",
)
USER_AGENT = f"Lumora/{__version__} (personal desktop client)"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 25
#: Catalog pages fetched ahead of the page the caller is working on.  The API
#: takes 2-40 s per 25-record page, so a sequential scan is the bottleneck, and
#: three or four requests in flight measure 2-4x the throughput of one.  More is
#: worse: six concurrent pages measurably slowed the server per request (median
#: latency 25-37 s against 14-16 s at three), so this stays small and bounded.
DEFAULT_PREFETCH = 4
MAX_PREFETCH = 6
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_CACHE_SECONDS = 300.0
#: The server is slow for large pages; keep a polite gap between requests.
DEFAULT_INTERVAL = 0.25
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_ARCHIVE_SUFFIXES = (
    ".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".iso",
    ".exe", ".msi", ".appimage", ".bin",
)

#: ``kind`` values a :class:`KryotoError` may carry.  The distinction matters:
#: an empty result is not an unsupported endpoint, and a 404 is not an outage.
ERROR_KINDS = (
    "timeout", "network", "rate_limited", "blocked", "not_found",
    "server_error", "malformed", "unsupported", "oversized", "refused",
)


class KryotoError(AppError):
    """A Kryoto request failed, with a machine-readable reason."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "network",
        status: int = 0,
        retry_after: float | None = None,
        url: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "network"
        self.status = int(status or 0)
        self.retry_after = retry_after
        self.url = url


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------

def resolve_game_url(raw: Any, *, base: str = KRYOTO_ORIGIN) -> str:
    """Resolve a source URL without normalising its query or fragment.

    Relative URLs are resolved against the API origin.  The returned value is
    the absolute URL used for opening or checking; callers keep ``raw_url`` so
    the provider's original spelling is never lost.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        split = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    if split.scheme:
        if split.scheme.lower() not in ("http", "https"):
            return ""
        if not split.netloc or split.username or split.password:
            return ""
        return text
    if split.netloc:  # protocol-relative ``//host/path``
        return "https:" + text
    try:
        resolved = urllib.parse.urljoin(base.rstrip("/") + "/", text)
    except ValueError:
        return ""
    final = urllib.parse.urlsplit(resolved)
    if final.scheme.lower() not in ("http", "https") or not final.netloc:
        return ""
    if final.username or final.password:
        return ""
    return resolved


def _host_variants(hosts: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(host).strip().lower().rstrip(".") for host in hosts if str(host).strip())


def host_allowed(url: str, hosts: Sequence[str]) -> bool:
    """True when *url* is HTTPS on one of the declared hosts."""
    try:
        split = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if split.scheme.lower() != "https" or split.username or split.password:
        return False
    host = (split.hostname or "").lower().rstrip(".")
    return bool(host) and any(
        host == allowed or host.endswith("." + allowed)
        for allowed in _host_variants(hosts)
    )


# --------------------------------------------------------------------------
# Parsed records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class KryotoLink:
    """One link exactly as the API published it."""

    raw_url: str
    url: str
    host: str = ""
    name: str = ""
    label: str = ""
    release_id: str = ""
    version: str = ""
    sha256: str = ""
    size: int | None = None
    primary: bool = False
    archived: bool = False
    #: ``provider-endpoint``, ``file-candidate``, ``webpage`` or ``unknown``.
    kind: str = "unknown"

    @property
    def display_label(self) -> str:
        """``label``, then ``name``, then ``host`` — never an invented name."""
        return self.label or self.name or self.host or self.url


@dataclass(frozen=True)
class KryotoGame:
    """A normalized game record from the catalog or detail endpoint."""

    slug: str
    title: str
    developer: str = ""
    year: str = ""
    genres: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    description: str = ""
    cover_url: str = ""
    source_url: str = ""
    version: str = ""
    size: str = ""
    download_size: str = ""
    links: tuple[KryotoLink, ...] = ()
    art: Mapping[str, Any] = field(default_factory=dict)
    related: tuple[Mapping[str, str], ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)
    detail_loaded: bool = False

    @property
    def has_source_links(self) -> bool:
        return bool(self.links)


@dataclass(frozen=True)
class KryotoPage:
    """One page of the offset-paginated catalog."""

    games: tuple[KryotoGame, ...]
    offset: int = 0
    limit: int = DEFAULT_PAGE_LIMIT
    received: int = 0
    #: ``True`` only when the server returned fewer records than requested.
    complete: bool = True
    url: str = ""
    notes: tuple[str, ...] = ()

    @property
    def next_offset(self) -> int | None:
        return None if self.complete else self.offset + self.received


@dataclass(frozen=True)
class KryotoDownloads:
    slug: str
    title: str = ""
    version: str = ""
    download_size: str = ""
    executable: str = ""
    links: tuple[KryotoLink, ...] = ()
    releases: tuple[Mapping[str, Any], ...] = ()
    hashes: tuple[Mapping[str, Any], ...] = ()

    @property
    def has_links(self) -> bool:
        return bool(self.links)


@dataclass(frozen=True)
class KryotoMirror:
    """A mirror-building job, not a download URL."""

    release_id: str
    host: str = ""
    state: str = ""
    progress: Any = None
    phase: Any = None


@dataclass(frozen=True)
class LinkCheck:
    """The outcome of one HTTP probe of a source link."""

    url: str
    #: A :class:`~lgl.catalog_store.MirrorRecord` status spelling.
    status: str = "unchecked"
    #: ``file``, ``page``, ``unverified``, ``unknown``.
    kind: str = "unknown"
    http_status: int = 0
    content_type: str = ""
    filename: str = ""
    size: int | None = None
    notes: tuple[str, ...] = ()
    checked_at: float = 0.0


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def _text(value: Any, limit: int = 400) -> str:
    if isinstance(value, str):
        return meta.clean_text(value, limit)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _list_text(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return meta.clean_list([item for item in value if isinstance(item, str)])
    if isinstance(value, str):
        return meta.clean_list(value)
    return ()


def _description(value: Any) -> str:
    if isinstance(value, list):
        parts = [part for part in value if isinstance(part, str)]
        return meta.clean_description("\n\n".join(parts))
    return meta.clean_description(value)


def _url_field(value: Any) -> str:
    return meta.clean_url(value)


def _classify_link(url: str, host: str = "") -> str:
    """Classify by shape only.  This never claims a link is downloadable."""
    try:
        split = urllib.parse.urlsplit(url)
    except ValueError:
        return "unknown"
    path = split.path.lower()
    if path.startswith("/api/download"):
        return "provider-endpoint"
    if any(path.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        return "file-candidate"
    if path in ("", "/"):
        return "webpage"
    return "webpage"


def is_downloadable_link(status: object, url_kind: object) -> bool:
    """May this catalog link be handed to the download manager?

    Only a probe that actually observed a file response qualifies.  A URL whose
    *shape* is a file candidate is not enough, and a page that answered 200 is
    not a download; both stay out of aria2 and belong to "Open in Browser".
    """
    return str(status or "") == "verified" and str(url_kind or "") == "file"


def _link_host(url: str, declared: str = "") -> str:
    if declared.strip():
        return meta.clean_text(declared, 120)
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def parse_link(
    raw: Any,
    *,
    base: str = KRYOTO_ORIGIN,
    release_id: str = "",
    version: str = "",
    sha256: str = "",
    size: int | None = None,
    primary: bool = False,
    archived: bool = False,
) -> KryotoLink | None:
    """Parse one ``links`` entry, preserving its URL byte for byte in ``raw_url``."""
    if not isinstance(raw, Mapping):
        return None
    raw_url = raw.get("url")
    if not isinstance(raw_url, str):
        return None
    url = resolve_game_url(raw_url, base=base)
    if not url:
        return None
    host = _link_host(url, _text(raw.get("host"), 120))
    return KryotoLink(
        raw_url=raw_url,
        url=url,
        host=host,
        name=_text(raw.get("name"), 260),
        label=_text(raw.get("label"), 260),
        release_id=release_id,
        version=version,
        sha256=sha256,
        size=size,
        primary=primary,
        archived=archived,
        kind=_classify_link(url, host),
    )


def _hash_table(hashes: Any) -> dict[str, Mapping[str, Any]]:
    table: dict[str, Mapping[str, Any]] = {}
    if isinstance(hashes, list):
        for item in hashes:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                table[name.strip()] = item
    return table


def _link_with_hash(
    link: KryotoLink, hashes: Mapping[str, Mapping[str, Any]]
) -> KryotoLink:
    entry = hashes.get(link.name) if link.name else None
    if (
        not entry
        and not link.name
        and len(hashes) == 1
        and link.kind in ("provider-endpoint", "file-candidate")
    ):
        entry = next(iter(hashes.values()))
    if not entry:
        return link
    digest = _text(entry.get("sha256"), 128)
    raw_size = entry.get("bytes")
    byte_count = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None
    return KryotoLink(
        raw_url=link.raw_url,
        url=link.url,
        host=link.host,
        name=link.name,
        label=link.label,
        release_id=link.release_id,
        version=link.version,
        sha256=digest or link.sha256,
        size=byte_count if byte_count and byte_count > 0 else link.size,
        primary=link.primary,
        archived=link.archived,
        kind=link.kind,
    )


def _dedupe_links(links: Iterable[KryotoLink]) -> tuple[KryotoLink, ...]:
    """De-duplicate by resolved URL, folding labels and checksums into the first."""
    kept: list[KryotoLink] = []
    index: dict[str, int] = {}
    for link in links:
        position = index.get(link.url)
        if position is None:
            index[link.url] = len(kept)
            kept.append(link)
            continue
        first = kept[position]
        kept[position] = KryotoLink(
            raw_url=first.raw_url,
            url=first.url,
            host=first.host or link.host,
            name=first.name or link.name,
            label=first.label or link.label,
            release_id=first.release_id or link.release_id,
            version=first.version or link.version,
            sha256=first.sha256 or link.sha256,
            size=first.size if first.size is not None else link.size,
            primary=first.primary or link.primary,
            archived=first.archived and link.archived,
            kind=first.kind if first.kind != "unknown" else link.kind,
        )
    return tuple(kept)


def _scalar_fields(game: Mapping[str, Any]) -> dict[str, Any]:
    """A bounded, informational copy of the provider's own scalar fields."""
    skip = {
        "description", "description_html", "links", "related", "art",
        "screenshots", "cover", "cover_horizontal", "cover_vertical",
        "system_requirements", "min_requirements", "recommended_requirements",
        "linux_system_requirements", "min_requirements", "game_launch_options",
        "dlc", "features", "genres", "tags", "platforms",
    }
    kept: dict[str, Any] = {}
    for key, value in game.items():
        name = str(key)
        if name in skip or len(name) > 48:
            continue
        if isinstance(value, bool):
            kept[name] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            kept[name] = value
        elif isinstance(value, str):
            text = meta.clean_text(value, 240)
            if text:
                kept[name] = text
        if len(kept) >= 40:
            break
    return kept


def _art_urls(art: Any, game: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return a display cover and the preserved art object."""
    art_map: dict[str, Any] = {}
    if isinstance(art, Mapping):
        for key, value in art.items():
            if key == "screenshots":
                shots = [meta.clean_url(item) for item in value] if isinstance(value, list) else []
                art_map[key] = [item for item in shots if item]
            else:
                cleaned = _url_field(value)
                if cleaned:
                    art_map[str(key)] = cleaned
    cover = (
        art_map.get("header")
        or art_map.get("capsule")
        or _url_field(game.get("cover_horizontal"))
        or _url_field(game.get("cover"))
        or _url_field(game.get("cover_vertical"))
    )
    shots = art_map.get("screenshots")
    if not isinstance(shots, list):
        raw_shots = game.get("screenshots")
        shots = (
            [url for url in (meta.clean_url(item) for item in raw_shots) if url]
            if isinstance(raw_shots, list) else []
        )
        if shots:
            art_map["screenshots"] = shots
    return cover or "", art_map


def _related(raw: Any) -> tuple[Mapping[str, str], ...]:
    related: list[Mapping[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            slug = _text(item.get("slug"), 160)
            title = _text(item.get("title"), 200)
            if slug or title:
                related.append({"slug": slug, "title": title})
            if len(related) >= 12:
                break
    return tuple(related)


def parse_game_record(
    raw: Any,
    *,
    base: str = KRYOTO_ORIGIN,
    links: Sequence[Any] | None = None,
    detail: bool = False,
) -> KryotoGame | None:
    """Parse a full record or a ``{"game": ...}`` detail document.

    Missing, null or wrongly typed fields become empty values.  A record without
    a slug or title is not usable and returns ``None``; nothing is invented.
    """
    if isinstance(raw, Mapping) and isinstance(raw.get("game"), Mapping):
        document = raw
        game = document["game"]
        detail = True
    elif isinstance(raw, Mapping):
        document = {}
        game = raw
    else:
        return None
    slug = _text(game.get("slug"), 160)
    title = _text(game.get("title"), 300)
    if not slug or not title:
        return None
    art = document.get("art") if isinstance(document, Mapping) else None
    cover, art_map = _art_urls(art, game)
    if not cover:
        cover = _url_field(game.get("cover_vertical"))
    raw_links = links if links is not None else game.get("links")
    parsed_links = [
        link for link in (
            parse_link(item, base=base) for item in raw_links
        ) if link is not None
    ] if isinstance(raw_links, list) else []
    hashes = _hash_table(game.get("hashes"))
    parsed_links = [_link_with_hash(link, hashes) for link in parsed_links]
    extra: dict[str, Any] = {
        "source_fields": _scalar_fields(game),
        "version": _text(game.get("version"), 80),
        "size": _text(game.get("size"), 40),
        "download_size": _text(game.get("download_size"), 40),
        "platforms": list(_list_text(game.get("platforms"))),
        "tags": list(_list_text(game.get("tags"))),
    }
    if art_map:
        extra["art"] = art_map
        if isinstance(art_map.get("screenshots"), list):
            extra["screenshots"] = list(art_map["screenshots"])
    related = _related(document.get("related") if isinstance(document, Mapping) else None)
    if related:
        extra["related"] = [dict(item) for item in related]
    return KryotoGame(
        slug=slug,
        title=title,
        developer=_text(game.get("developer"), 300),
        year=meta.clean_year(game.get("year")),
        genres=_list_text(game.get("genres")),
        tags=_list_text(game.get("tags")),
        platforms=_list_text(game.get("platforms")),
        description=_description(game.get("description")),
        cover_url=cover,
        source_url=f"{KRYOTO_ORIGIN}/games/{urllib.parse.quote(slug)}",
        version=_text(game.get("version"), 80),
        size=_text(game.get("size"), 40),
        download_size=_text(game.get("download_size"), 40),
        links=_dedupe_links(parsed_links),
        art=art_map,
        related=related,
        extra=extra,
        detail_loaded=detail,
    )


def parse_downloads(
    raw: Any, *, base: str = KRYOTO_ORIGIN
) -> KryotoDownloads:
    """Parse the downloads endpoint, including versioned releases and hashes."""
    if not isinstance(raw, Mapping):
        return KryotoDownloads(slug="")
    slug = _text(raw.get("slug"), 160)
    hashes_raw = raw.get("hashes")
    hashes = _hash_table(hashes_raw)
    collected: list[KryotoLink] = []

    def add(items: Any, table: Mapping[str, Mapping[str, Any]], *,
            release_id: str = "", version: str = "",
            primary: bool = False, archived: bool = False) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            link = parse_link(
                item, base=base, release_id=release_id, version=version,
                primary=primary, archived=archived,
            )
            if link is not None:
                collected.append(_link_with_hash(link, table))

    add(raw.get("links"), hashes)
    releases: list[Mapping[str, Any]] = []
    raw_releases = raw.get("releases")
    if isinstance(raw_releases, list):
        for item in raw_releases:
            if not isinstance(item, Mapping):
                continue
            release = {
                "id": _text(item.get("id"), 40),
                "version": _text(item.get("version"), 80),
                "label": _text(item.get("label"), 120),
                "source": _text(item.get("source"), 120),
                "download_size": _text(item.get("download_size"), 40),
                "created_at": _text(item.get("created_at"), 40),
                "primary": bool(item.get("primary")),
                "archived": bool(item.get("archived")),
            }
            releases.append(release)
            release_hashes = _hash_table(item.get("hashes")) or hashes
            add(
                item.get("links"), release_hashes,
                release_id=release["id"],
                version=release["version"],
                primary=release["primary"],
                archived=release["archived"],
            )
    hash_list: list[Mapping[str, Any]] = []
    if isinstance(hashes_raw, list):
        for item in hashes_raw:
            if not isinstance(item, Mapping):
                continue
            size = item.get("bytes")
            hash_list.append({
                "name": _text(item.get("name"), 260),
                "sha256": _text(item.get("sha256"), 128),
                "bytes": size if isinstance(size, int) and not isinstance(size, bool) else None,
            })
    return KryotoDownloads(
        slug=slug,
        title=_text(raw.get("title"), 300),
        version=_text(raw.get("version"), 80),
        download_size=_text(raw.get("download_size"), 40),
        executable=_text(raw.get("executable"), 260),
        links=_dedupe_links(collected),
        releases=tuple(releases),
        hashes=tuple(hash_list),
    )


def parse_mirrors(raw: Any) -> tuple[KryotoMirror, ...]:
    """Parse the mirrors endpoint, which describes mirror jobs, not files."""
    if not isinstance(raw, Mapping):
        return ()
    table = raw.get("mirrors")
    if not isinstance(table, Mapping):
        return ()
    mirrors: list[KryotoMirror] = []
    for release_id, items in table.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            mirrors.append(
                KryotoMirror(
                    release_id=_text(release_id, 40),
                    host=_text(item.get("host"), 80),
                    state=_text(item.get("state"), 40),
                    progress=item.get("progress"),
                    phase=item.get("phase"),
                )
            )
    return tuple(mirrors)


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------

Transport = Callable[[str, float], tuple[int, Mapping[str, str], bytes, str]]


_CONNECTIONS = threading.local()


def _close_connection() -> None:
    connection = getattr(_CONNECTIONS, "connection", None)
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass
    _CONNECTIONS.connection = None
    _CONNECTIONS.key = None


def _default_transport(url: str, timeout: float) -> tuple[int, Mapping[str, str], bytes, str]:
    """One HTTPS request with the project user agent, reusing one connection.

    A per-thread keep-alive connection avoids a fresh TLS handshake for every
    page/detail request.  The connection is discarded when the server says it
    will close, when the host changes, or after any transport error.
    """
    split = urllib.parse.urlsplit(url)
    if split.scheme != "https" or not split.hostname:
        raise OSError("Kryoto requests require an https host.")
    key = (split.hostname.lower(), split.port or 443)
    connection = getattr(_CONNECTIONS, "connection", None)
    if connection is None or getattr(_CONNECTIONS, "key", None) != key:
        _close_connection()
        connection = http.client.HTTPSConnection(
            key[0], key[1], timeout=timeout
        )
        _CONNECTIONS.connection = connection
        _CONNECTIONS.key = key
    else:
        connection.timeout = timeout
        if connection.sock is not None:
            try:
                connection.sock.settimeout(timeout)
            except OSError:
                _close_connection()
                return _default_transport(url, timeout)
    target = split.path or "/"
    if split.query:
        target += "?" + split.query
    try:
        connection.request(
            "GET", target,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        status = int(response.status)
        headers = {name: value for name, value in response.getheaders()}
        final_url = url
        location = headers.get("location") or headers.get("Location")
        if status in (301, 302, 303, 307, 308) and location:
            final_url = urllib.parse.urljoin(url, location)
        if response.will_close:
            _close_connection()
        return status, headers, body, final_url
    except (OSError, http.client.HTTPException, TimeoutError):
        _close_connection()
        raise


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = ""
    for key, value in headers.items():
        if key.lower() == "retry-after":
            raw = str(value).strip()
            break
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        import email.utils

        try:
            when = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        seconds = when.timestamp() - time.time()
    return max(0.0, min(60.0, seconds))


class KryotoClient:
    """Offset-paginated, rate-limited, retrying Kryoto API client."""

    def __init__(
        self,
        *,
        origin: str = KRYOTO_ORIGIN,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        min_interval: float = DEFAULT_INTERVAL,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.origin = origin.rstrip("/")
        self.api = f"{self.origin}/api"
        self.timeout = max(1.0, float(timeout))
        self.max_retries = max(0, min(5, int(max_retries)))
        self.min_interval = max(0.0, float(min_interval))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._transport = transport or _default_transport
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._last_request = 0.0
        self.requests_made = 0
        self.retries = 0
        self.last_error = ""

    # -- cache --------------------------------------------------------------

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def cache_entries(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    # -- request ------------------------------------------------------------

    def _wait(self) -> None:
        with self._request_lock:
            now = self._clock()
            delay = self.min_interval - (now - self._last_request)
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
            self._last_request = now
            self.requests_made += 1

    def _get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        force: bool = False,
        cache_seconds: float | None = None,
    ) -> Any:
        query = urllib.parse.urlencode(
            [(key, value) for key, value in (params or {}).items() if value is not None]
        )
        url = f"{self.api}{path}" + (f"?{query}" if query else "")
        if not host_allowed(url, KRYOTO_HOSTS):
            raise KryotoError(
                f"Refusing a request outside {KRYOTO_ORIGIN}: {url}",
                kind="refused", url=url,
            )
        ttl = self.cache_seconds if cache_seconds is None else max(0.0, cache_seconds)
        now = self._clock()
        if not force and ttl > 0:
            with self._cache_lock:
                cached = self._cache.get(url)
            if cached is not None and now - cached[0] <= ttl:
                return copy.deepcopy(cached[1])

        last: KryotoError | None = None
        for attempt in range(self.max_retries + 1):
            self._wait()
            try:
                status, headers, body, final_url = self._transport(url, self.timeout)
            except (TimeoutError, OSError) as exc:
                last = KryotoError(
                    f"Could not reach {url}: {exc}", kind="timeout", url=url
                )
            else:
                if not host_allowed(final_url, KRYOTO_HOSTS):
                    raise KryotoError(
                        f"{url} redirected outside {KRYOTO_ORIGIN}.",
                        kind="blocked", status=status, url=url,
                    )
                if len(body) > MAX_RESPONSE_BYTES:
                    raise KryotoError(
                        f"{url} returned more than {MAX_RESPONSE_BYTES} bytes.",
                        kind="oversized", status=status, url=url,
                    )
                if status == 200:
                    try:
                        document = json.loads(body.decode("utf-8", "replace"))
                    except ValueError as exc:
                        raise KryotoError(
                            f"{url} did not return valid JSON.", kind="malformed",
                            status=status, url=url,
                        ) from exc
                    with self._cache_lock:
                        self._cache[url] = (self._clock(), copy.deepcopy(document))
                    return document
                if status == 404:
                    raise KryotoError(
                        f"{url} was not found.", kind="not_found", status=404, url=url
                    )
                if status in (401, 403):
                    raise KryotoError(
                        f"{url} answered HTTP {status}; access was refused.",
                        kind="blocked", status=status, url=url,
                    )
                if status in _RETRY_STATUSES:
                    wait = _retry_after(headers)
                    kind = "rate_limited" if status == 429 else "server_error"
                    last = KryotoError(
                        f"{url} answered HTTP {status}.", kind=kind,
                        status=status, retry_after=wait, url=url,
                    )
                    if attempt < self.max_retries:
                        self.retries += 1
                        self._sleep(wait if wait is not None else min(
                            8.0, 0.5 * (2 ** attempt) + random.uniform(0.0, 0.25)
                        ))
                        continue
                else:
                    raise KryotoError(
                        f"{url} answered HTTP {status}.", kind="server_error",
                        status=status, url=url,
                    )
            if last is not None and attempt < self.max_retries:
                self.retries += 1
                self._sleep(min(8.0, 0.5 * (2 ** attempt) + random.uniform(0.0, 0.25)))
                continue
            break
        self.last_error = str(last) if last else "Kryoto request failed."
        raise last or KryotoError(self.last_error, kind="network", url=url)

    # -- endpoints ----------------------------------------------------------

    def list_games(
        self, offset: int = 0, limit: int = DEFAULT_PAGE_LIMIT, *, force: bool = False
    ) -> KryotoPage:
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        document = self._get("/games", {"limit": limit, "offset": offset}, force=force)
        if not isinstance(document, Mapping) or not isinstance(document.get("games"), list):
            raise KryotoError(
                "The catalog response did not contain a games list.",
                kind="malformed", url=f"{self.api}/games",
            )
        raw_games = document.get("games")
        games = [
            game for game in (
                parse_game_record(item, base=self.origin) for item in raw_games
            ) if game is not None
        ]
        notes: tuple[str, ...] = ()
        if len(games) < len(raw_games):
            notes = (
                f"{len(raw_games) - len(games)} record(s) had no usable slug or title.",
            )
        return KryotoPage(
            games=tuple(games),
            offset=offset,
            limit=limit,
            received=len(raw_games),
            complete=len(raw_games) < limit,
            url=f"{self.api}/games?limit={limit}&offset={offset}",
            notes=notes,
        )

    def game(self, slug: str, *, force: bool = False) -> KryotoGame:
        slug = _text(slug, 160)
        if not slug:
            raise KryotoError("A slug is needed to fetch a game.", kind="refused")
        document = self._get(
            f"/games/{urllib.parse.quote(slug)}", force=force
        )
        game = parse_game_record(document, base=self.origin, detail=True)
        if game is None:
            raise KryotoError(
                f"Kryoto returned no usable game for {slug}.", kind="malformed"
            )
        return game

    def downloads(self, slug: str, *, force: bool = False) -> KryotoDownloads:
        slug = _text(slug, 160)
        if not slug:
            raise KryotoError("A slug is needed to fetch downloads.", kind="refused")
        document = self._get(
            f"/games/{urllib.parse.quote(slug)}/downloads", force=force
        )
        downloads = parse_downloads(document, base=self.origin)
        if not downloads.slug:
            raise KryotoError(
                f"Kryoto returned no download record for {slug}.", kind="malformed"
            )
        return downloads

    def mirrors(self, slug: str, *, force: bool = False) -> tuple[KryotoMirror, ...]:
        slug = _text(slug, 160)
        if not slug:
            raise KryotoError("A slug is needed to fetch mirrors.", kind="refused")
        document = self._get(
            f"/games/{urllib.parse.quote(slug)}/mirrors", force=force
        )
        return parse_mirrors(document)

    def search(
        self, query: str, limit: int = 10, *, force: bool = False
    ) -> tuple[KryotoGame, ...]:
        text = _text(query, 120)
        if not text:
            return ()
        limit = max(1, min(int(limit), MAX_PAGE_LIMIT))
        document = self._get(
            "/games/search", {"q": text, "limit": limit}, force=force
        )
        if not isinstance(document, Mapping) or not isinstance(document.get("results"), list):
            raise KryotoError(
                "The search response did not contain a results list.",
                kind="malformed",
            )
        return tuple(
            game for game in (
                parse_game_record(item, base=self.origin) for item in document["results"]
            ) if game is not None
        )


# --------------------------------------------------------------------------
# Link checking
# --------------------------------------------------------------------------

def _probe(url: str, *, timeout: float, allowed_hosts: Sequence[str]):
    from . import catalog

    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        raise KryotoError("That link has no host.", kind="refused")
    catalog.ensure_public_host(host)
    policy = catalog.SourcePolicy(
        name="Kryoto source link check",
        hosts=frozenset(_host_variants(allowed_hosts) or (host,)),
        permitted_reason=(
            "The user asked to check a link the selected catalog published."
        ),
        timeout=max(3.0, float(timeout)),
        min_delay=0.0,
        respect_robots=True,
    )
    return catalog.probe_link(catalog.build_fetcher(policy), url)


def check_link(
    url: str,
    *,
    timeout: float = 12.0,
    allow_external: bool = False,
    allowed_hosts: Sequence[str] = KRYOTO_HOSTS,
) -> LinkCheck:
    """Probe one source link without downloading it.

    ``allow_external=False`` refuses to make a request to a host outside the
    provider's own hosts and reports the link as un-checked instead.  A bulk
    check therefore stays inside Kryoto; an individual check may opt in.
    """
    from . import catalog

    if not isinstance(url, str) or not url.strip():
        return LinkCheck(url=str(url or ""), status="unsupported", notes=("Empty URL.",))
    if not url.lower().startswith("https://"):
        return LinkCheck(
            url=url, status="unsupported",
            notes=("Only https:// links can be checked.",),
        )
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return LinkCheck(url=url, status="unsupported", notes=("The URL is unreadable.",))
    if not host:
        return LinkCheck(url=url, status="unsupported", notes=("The URL has no host.",))
    if not allow_external and not host_allowed(url, allowed_hosts):
        return LinkCheck(
            url=url, status="external",
            notes=(
                f"{host} is not a Kryoto host, so it was not checked in the bulk "
                "pass. Use the per-link Check action to probe it directly.",
            ),
            checked_at=time.time(),
        )
    try:
        probe = _probe(url, timeout=timeout, allowed_hosts=(host,))
    except catalog.AccessDeniedError as exc:
        text = str(exc)
        status = "blocked" if "robots" in text.lower() or "refused" in text.lower() else "failed"
        return LinkCheck(
            url=url, status=status, kind="unknown", notes=(text,),
            checked_at=time.time(),
        )
    except catalog.CatalogError as exc:
        return LinkCheck(
            url=url, status="failed", kind="unknown", notes=(str(exc),),
            checked_at=time.time(),
        )
    except (OSError, ValueError) as exc:
        return LinkCheck(
            url=url, status="failed", kind="unknown",
            notes=(f"The link could not be checked: {exc}",), checked_at=time.time(),
        )
    kind = probe.kind
    if kind == catalog.LinkKind.VERIFIED_FILE:
        status = "verified"
        link_kind = "file"
    elif kind in (catalog.LinkKind.PAGE, catalog.LinkKind.LANDING):
        status = "page"
        link_kind = "page"
    elif kind == catalog.LinkKind.EXTERNAL:
        status = "external"
        link_kind = "page"
    elif kind == catalog.LinkKind.UNREACHABLE:
        status = "missing" if probe.status in (404, 410) else "failed"
        link_kind = "unknown"
    else:
        status = "unchecked"
        link_kind = "unverified"
    return LinkCheck(
        url=url,
        status=status,
        kind=link_kind,
        http_status=probe.status,
        content_type=probe.content_type,
        filename=probe.filename,
        size=probe.size,
        notes=probe.notes,
        checked_at=time.time(),
    )


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_PREFETCH",
    "MAX_PREFETCH",
    "ERROR_KINDS",
    "is_downloadable_link",
    "KRYOTO_API",
    "KRYOTO_HOSTS",
    "KRYOTO_IMAGE_HOSTS",
    "KRYOTO_ORIGIN",
    "KryotoClient",
    "KryotoDownloads",
    "KryotoError",
    "KryotoGame",
    "KryotoLink",
    "KryotoMirror",
    "KryotoPage",
    "LinkCheck",
    "check_link",
    "host_allowed",
    "parse_downloads",
    "parse_game_record",
    "parse_link",
    "parse_mirrors",
    "resolve_game_url",
]
