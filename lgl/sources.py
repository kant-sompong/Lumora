"""Website source adapters: capability reporting, paging, and normalization.

A *source adapter* is code, not a crawler configuration.  Each adapter knows one
website's publicly documented interfaces, says plainly which operations it has
actually verified, and refuses the rest.  Nothing here guesses an endpoint: a
capability is ``VERIFIED`` only after a request in this session returned a
response whose shape is what the adapter expects.

Two rules shape the design:

* an adapter can only talk to hosts it declares, over HTTPS, with redirects
  validated hop by hop, a timeout, and a response-size cap;
* anything the source does not expose (magnets on archive.org, for example) is
  reported as ``UNSUPPORTED`` with a reason instead of being faked.

The first adapter targets the Internet Archive's public search and metadata
endpoints, which the application already uses elsewhere.  It is a lawful,
documented source and is useful precisely because it has thousands of items,
real pagination, real artwork, and no magnet or mirror-payload shenanigans.
"""

from __future__ import annotations

import abc
import hashlib
import html as html_module
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import __version__
from . import kryoto
from . import metadata as meta
from .filetypes import is_auxiliary
from .util import AppError, human_size, strip_html, validate_identifier

USER_AGENT = f"linux-game-library/{__version__} (personal desktop client)"
ARCHIVE_HOST = "archive.org"
ARCHIVE_BASE = "https://archive.org"
DEFAULT_COLLECTION = "native-linux-games-collection"
MAX_JSON_BYTES = 8 * 1024 * 1024
#: How much of a refused response's body is read to find the provider's reason.
_ERROR_BODY_BYTES = 4096
DEFAULT_TIMEOUT = 30.0
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_IMAGE_TOKENS = ("cover", "poster", "boxart", "box_art", "capsule", "icon", "logo", "artwork")


class SourceError(AppError):
    """A source could not be inspected or read.

    A provider refuses a request for reasons that have nothing to do with the
    record being asked about: a rejected key, a spent quota, a throttled
    client.  The status and the provider's own words are kept so a caller can
    tell those apart from "this game is not in the catalog".
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail

    @property
    def stops_run(self) -> bool:
        """True when repeating this request cannot work, so a bulk run stops.

        A rejected key (401/403) or an exhausted quota answered as one, and a
        client the provider is throttling (429), say nothing about the next
        game.  Asking again for every record only turns one real error into
        thousands of meaningless "failed" lookups.
        """
        return self.status in (401, 403, 429)


class UnsupportedOperation(SourceError):
    """The source does not implement this operation at all."""


class Capability(str, Enum):
    """How sure we are about one source operation."""

    VERIFIED = "verified"
    PROBABLE = "probable"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            Capability.VERIFIED: "verified",
            Capability.PROBABLE: "probable",
            Capability.UNSUPPORTED: "unsupported",
            Capability.UNKNOWN: "unknown",
        }[self]


@dataclass(frozen=True)
class CapabilityReport:
    """What a source can actually do right now, with reasons for the gaps."""

    catalog: Capability = Capability.UNKNOWN
    pagination: Capability = Capability.UNKNOWN
    details: Capability = Capability.UNKNOWN
    artwork: Capability = Capability.UNKNOWN
    magnets: Capability = Capability.UNKNOWN
    mirrors: Capability = Capability.UNKNOWN
    total_estimate: int | None = None
    rate_limit: str = ""
    reasons: Mapping[str, str] = field(default_factory=dict)
    checked_at: float = 0.0

    def state(self, feature: str) -> Capability:
        value = getattr(self, feature, Capability.UNKNOWN)
        return value if isinstance(value, Capability) else Capability.UNKNOWN

    def rows(self) -> list[tuple[str, str, str]]:
        """(feature, state, explanation) rows for the preview panel."""
        labels = (
            ("catalog", "Catalog discovery"),
            ("pagination", "Pagination"),
            ("details", "Game details"),
            ("artwork", "Artwork"),
            ("magnets", "Magnet discovery"),
            ("mirrors", "Mirror discovery"),
        )
        rows: list[tuple[str, str, str]] = []
        for feature, label in labels:
            state = self.state(feature)
            reason = self.reasons.get(feature, "")
            if state == Capability.VERIFIED and not reason:
                reason = "Confirmed by a live response in this session."
            elif state == Capability.UNSUPPORTED and not reason:
                reason = "This source does not expose it."
            elif state == Capability.UNKNOWN and not reason:
                reason = "Not checked yet."
            rows.append((label, state.label, reason))
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog": self.catalog.value,
            "pagination": self.pagination.value,
            "details": self.details.value,
            "artwork": self.artwork.value,
            "magnets": self.magnets.value,
            "mirrors": self.mirrors.value,
            "total_estimate": self.total_estimate,
            "rate_limit": self.rate_limit,
            "reasons": dict(self.reasons),
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CapabilityReport":
        if not isinstance(raw, Mapping):
            return cls()

        def state(name: str) -> Capability:
            value = raw.get(name)
            try:
                return Capability(str(value))
            except ValueError:
                return Capability.UNKNOWN

        total = raw.get("total_estimate")
        reasons = raw.get("reasons")
        return cls(
            catalog=state("catalog"),
            pagination=state("pagination"),
            details=state("details"),
            artwork=state("artwork"),
            magnets=state("magnets"),
            mirrors=state("mirrors"),
            total_estimate=total if isinstance(total, int) and total >= 0 else None,
            rate_limit=str(raw.get("rate_limit") or ""),
            reasons={
                str(k): str(v) for k, v in reasons.items()
            } if isinstance(reasons, Mapping) else {},
            checked_at=float(raw.get("checked_at") or 0.0),
        )


@dataclass
class SourceGame:
    """One normalized game record from a source."""

    source_id: str
    game_id: str
    title: str
    year: str = ""
    creator: str = ""
    developer: str = ""
    publisher: str = ""
    release_date: str = ""
    genres: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    description: str = ""
    cover_url: str = ""
    source_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self, provider: str = "") -> meta.GameMetadata:
        return meta.GameMetadata.from_mapping(
            {
                "title": self.title,
                "creator": self.creator,
                "year": self.year,
                "developer": self.developer,
                "publisher": self.publisher,
                "release_date": self.release_date,
                "genres": list(self.genres),
                "platforms": list(self.platforms),
                "description": self.description,
                "source_url": self.source_url or self.cover_url,
            },
            provider=provider or self.source_id,
        )


@dataclass
class SourceMirror:
    """A download URL as reported by the source (never guessed or probed here)."""

    url: str
    filename: str = ""
    size: int | None = None
    format: str = ""
    checksum_algorithm: str = ""
    checksum_value: str = ""
    version: str = ""
    #: The provider's own words for this link (``label``, then ``name``).
    label: str = ""
    #: The provider's declared host name, or the URL's host when it gave none.
    host: str = ""
    #: ``file-candidate``, ``provider-endpoint``, ``webpage`` or ``unknown``.
    #: A shape, not a claim that the link is downloadable.
    url_kind: str = ""
    #: The provider's original URL before relative resolution.
    raw_url: str = ""
    requires_auth: bool = False
    notes: tuple[str, ...] = ()


@dataclass
class SourcePage:
    """One page of a catalog, plus how to ask for the next one."""

    games: list[SourceGame]
    next_cursor: str | None = None
    total: int | None = None
    page_number: int = 1
    url: str = ""
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.next_cursor is None


class WebsiteSource(abc.ABC):
    """Adapter interface.  Unsupported operations raise, they never invent."""

    id: str = ""
    name: str = ""
    canonical_url: str = ""
    adapter_version: str = "1"
    hosts: tuple[str, ...] = ()
    #: Hosts approved for artwork URLs returned by this adapter.
    image_hosts: tuple[str, ...] = ()
    site: str = ""
    #: Shown in the import diagnostics once a request has been made.
    response_format: str = "unknown"
    pagination_method: str = "unknown"
    #: ``api``, ``catalog``, ``sitemap``, ``structured``, ``file``, or ``archive``.
    source_kind: str = "catalog"
    #: Description of the authentication needed, empty for anonymous sources.
    auth_kind: str = ""
    requires_auth: bool = False
    #: ``none``, ``store-links``, ``public-files``, or ``authorized-files``.
    download_capability: str = "none"
    #: Human-readable reason the source's published interfaces may be read.
    permitted_reason: str = ""
    #: URL and label an adapter's terms ask us to show next to its data.
    attribution: str = ""
    attribution_label: str = ""

    def __init__(self) -> None:
        self.last_error: str = ""

    # -- construction -------------------------------------------------------

    @classmethod
    def matches(cls, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return any(host == allowed or host.endswith("." + allowed) for allowed in cls.hosts)

    @classmethod
    def from_url(
        cls, url: str, *, api_keys: Mapping[str, str] | None = None
    ) -> "WebsiteSource":
        if not cls.matches(url):
            raise SourceError(f"This URL does not belong to {cls.name or cls.id}.")
        return cls()

    @property
    def url(self) -> str:
        return self.canonical_url

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.canonical_url,
            "adapter_version": self.adapter_version,
            "site": self.site,
            "source_kind": self.source_kind,
            "auth_kind": self.auth_kind,
            "requires_auth": self.requires_auth,
            "download_capability": self.download_capability,
            "permitted_reason": self.permitted_reason,
            "attribution": self.attribution,
        }

    # -- operations ---------------------------------------------------------

    @abc.abstractmethod
    def inspect(self) -> CapabilityReport:
        """Check what actually works now. Must not fabricate any result."""

    @abc.abstractmethod
    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        """Fetch one catalog page; ``cursor`` is whatever ``next_cursor`` was."""

    def details(self, game_id: str) -> SourceGame:
        raise UnsupportedOperation(f"{self.name} has no details interface here.")

    def magnets(self, game_id: str) -> list[Any]:
        raise UnsupportedOperation(f"{self.name} does not expose magnet links here.")

    def mirrors(self, game_id: str) -> list[SourceMirror]:
        raise UnsupportedOperation(f"{self.name} does not expose mirror files here.")


# --------------------------------------------------------------------------
# HTTP helper, restricted to declared hosts
# --------------------------------------------------------------------------

class _ValidatingRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave the declared hosts or the HTTPS scheme."""

    def __init__(self, hosts: Sequence[str]) -> None:
        super().__init__()
        self._hosts = tuple(hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _url_allowed(newurl, self._hosts):
            raise SourceError(f"The server redirected to a refused destination: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _url_allowed(url: str, hosts: Sequence[str]) -> bool:
    try:
        split = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if split.scheme.lower() != "https":
        return False
    if split.username or split.password:
        return False
    host = (split.hostname or "").lower()
    if not host or ":" in host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in hosts)


def _error_detail(raw: bytes) -> str:
    """The provider's own explanation of a refused request, when it gives one.

    Providers publish this in the error body, as JSON (``error``, ``detail``,
    ``message``) or as a line of text.  Anything unparseable is dropped rather
    than echoed: the message is shown to a person, not logged raw.
    """
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw)[:4096].decode("utf-8", "replace")
    else:
        text = str(raw or "")[:4096]
    text = text.strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("error", "detail", "message", "reason"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:200]
        return ""
    if text.startswith("{") or text.startswith("["):
        return ""
    return " ".join(text.split())[:200]


def request_json_document(
    url: str,
    *,
    hosts: Sequence[str],
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_JSON_BYTES,
) -> Any:
    """Fetch and parse a JSON document from an allowlisted HTTPS host.

    The document may be an object or an array; a caller that needs one shape
    checks it itself, so an array-returning endpoint is not misread as an error.
    """
    if not _url_allowed(url, hosts):
        raise SourceError(f"Refusing a request outside the declared hosts: {url}")
    opener = urllib.request.build_opener(_ValidatingRedirect(hosts))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            if status != 200:
                raise SourceError(f"{url} answered HTTP {status}.", status=status)
            content_type = str(response.headers.get("content-type") or "").lower()
            if "json" not in content_type:
                raise SourceError(
                    f"{url} answered {content_type or 'no content type'} instead of JSON."
                )
            raw = response.read(max_bytes + 1)
    except SourceError:
        raise
    except urllib.error.HTTPError as exc:
        # The body often says *why* ("The monthly API limit reached"), and that
        # is the only useful sentence here.
        try:
            detail = _error_detail(exc.read(_ERROR_BODY_BYTES))
        except OSError:
            detail = ""
        message = f"{url} answered HTTP {exc.code}"
        raise SourceError(
            f"{message}: {detail}." if detail else f"{message}.",
            status=int(exc.code),
            detail=detail,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"Could not reach {url}: {getattr(exc, 'reason', exc)}") from exc
    if len(raw) > max_bytes:
        raise SourceError(f"{url} returned more than {human_size(max_bytes)}.")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise SourceError(f"{url} did not return valid JSON.") from exc


def request_json(
    url: str,
    *,
    hosts: Sequence[str],
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_JSON_BYTES,
) -> dict[str, Any]:
    """Fetch and parse a JSON *object* from an allowlisted HTTPS host."""
    data = request_json_document(
        url, hosts=hosts, timeout=timeout, max_bytes=max_bytes
    )
    if not isinstance(data, dict):
        raise SourceError(f"{url} returned JSON that is not an object.")
    return data


# --------------------------------------------------------------------------
# Internet Archive adapter
# --------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = _as_text(item)
            if text:
                return text
    return ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _text_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return meta.clean_list(value)
    if isinstance(value, (list, tuple)):
        return meta.clean_list([item for item in value if isinstance(item, str)])
    return ()


def archive_collection_from_url(url: str) -> str:
    """Return the collection identifier a /details/ URL points at, or raise."""
    if not _url_allowed(url, (ARCHIVE_HOST,)):
        raise SourceError("Enter an https://archive.org/details/<identifier> URL.")
    split = urllib.parse.urlsplit(url)
    parts = [part for part in split.path.split("/") if part]
    if not parts:
        return DEFAULT_COLLECTION
    if parts[0] != "details" or len(parts) < 2:
        raise SourceError(
            "Use an archive.org /details/<identifier> URL, or the site root for the "
            "default collection."
        )
    try:
        validate_identifier(parts[1])
    except ValueError as exc:
        raise SourceError(f"That does not look like an archive.org identifier: {exc}") from exc
    return parts[1]


def _poster_url(identifier: str, files: Sequence[Mapping[str, Any]]) -> str:
    best: tuple[int, str] | None = None
    for raw in files:
        name = _as_text(raw.get("name")).strip()
        if not name or raw.get("private") is True:
            continue
        lowered = name.casefold()
        if not lowered.endswith(IMAGE_SUFFIXES):
            continue
        tokens = set(re.split(r"[^a-z0-9]+", lowered.rsplit(".", 1)[0]))
        if tokens & {"screenshot", "thumb", "thumbnail"}:
            continue
        score = 0
        for index, token in enumerate(_IMAGE_TOKENS):
            if token in tokens:
                score = max(score, len(_IMAGE_TOKENS) - index)
        if score <= 0:
            continue
        url = f"{ARCHIVE_BASE}/download/{identifier}/{urllib.parse.quote(name)}"
        if best is None or score > best[0]:
            best = (score, url)
    return best[1] if best else ""


class ArchiveOrgSource(WebsiteSource):
    """Public search + metadata endpoints of the Internet Archive."""

    id = "archive.org"
    name = "Internet Archive"
    canonical_url = "https://archive.org/details/native-linux-games-collection"
    adapter_version = "1"
    hosts = (ARCHIVE_HOST,)
    image_hosts = (ARCHIVE_HOST,)
    site = "https://archive.org"
    response_format = "JSON search API (response.docs)"
    pagination_method = "page number (page=N, rows=<limit>)"
    source_kind = "archive"
    download_capability = "public-files"
    permitted_reason = (
        "The Internet Archive publishes this collection's metadata and files for "
        "public access through documented endpoints."
    )

    def __init__(
        self,
        collection: str = DEFAULT_COLLECTION,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        fetch_json: Callable[..., dict[str, Any]] = request_json,
    ) -> None:
        super().__init__()
        validate_identifier(collection)
        self.collection = collection
        self.timeout = float(timeout)
        self._fetch_json = fetch_json
        self.canonical_url = f"{ARCHIVE_BASE}/details/{collection}"
        self._details_cache: dict[str, tuple[SourceGame, list[SourceMirror]]] = {}

    @classmethod
    def from_url(
        cls, url: str, *, api_keys: Mapping[str, str] | None = None
    ) -> "ArchiveOrgSource":
        collection = archive_collection_from_url(url)
        return cls(collection)

    # -- helpers ------------------------------------------------------------

    def _get(self, url: str) -> dict[str, Any]:
        try:
            return self._fetch_json(
                url, hosts=self.hosts, timeout=self.timeout
            )
        except AppError as exc:
            self.last_error = str(exc)
            raise
        except (OSError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise SourceError(f"{url} could not be read: {exc}") from exc

    def _search_url(self, page: int, limit: int) -> str:
        params: list[tuple[str, str]] = [
            ("q", f"collection:{self.collection}"),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "year"),
            ("fl[]", "date"),
            ("fl[]", "creator"),
            ("fl[]", "publisher"),
            ("fl[]", "subject"),
            ("fl[]", "description"),
            ("rows", str(limit)),
            ("page", str(page)),
            ("output", "json"),
            ("sort[]", "title asc"),
        ]
        return f"{ARCHIVE_BASE}/advancedsearch.php?" + urllib.parse.urlencode(params)

    def _game_from_doc(self, doc: Mapping[str, Any]) -> SourceGame | None:
        identifier = _as_text(doc.get("identifier")).strip()
        if not identifier:
            return None
        try:
            validate_identifier(identifier)
        except ValueError:
            return None
        title = meta.clean_text(_as_text(doc.get("title")) or identifier, 300)
        year = meta.clean_year(_as_text(doc.get("year")) or _as_text(doc.get("date")))
        creator = meta.clean_text(_as_text(doc.get("creator")))
        publisher = meta.clean_text(_as_text(doc.get("publisher")))
        return SourceGame(
            source_id=self.id,
            game_id=identifier,
            title=title or identifier,
            year=year,
            creator=creator,
            publisher=publisher,
            release_date=meta.clean_release_date(_as_text(doc.get("date"))),
            genres=_text_list(doc.get("subject")),
            description=meta.clean_description(_as_text(doc.get("description"))),
            cover_url="",
            source_url=f"{ARCHIVE_BASE}/details/{identifier}",
            extra={"collection": self.collection},
        )

    # -- protocol -----------------------------------------------------------

    def inspect(self) -> CapabilityReport:
        reasons: dict[str, str] = {}
        try:
            data = self._get(self._search_url(1, 1))
        except SourceError as exc:
            self.last_error = str(exc)
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNKNOWN,
                reasons={
                    "catalog": f"The search endpoint could not be read: {exc}",
                    "magnets": "This adapter does not extract infohashes from .torrent files.",
                },
            )
        response = data.get("response")
        docs = response.get("docs") if isinstance(response, Mapping) else None
        num_found = _as_int(response.get("numFound")) if isinstance(response, Mapping) else None
        if not isinstance(docs, list) or num_found is None:
            self.last_error = "The search response did not contain response.docs/numFound."
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                reasons={"catalog": self.last_error},
            )
        if num_found <= 0:
            return CapabilityReport(
                catalog=Capability.VERIFIED,
                pagination=Capability.VERIFIED,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNKNOWN,
                total_estimate=0,
                reasons={
                    "catalog": f"The collection “{self.collection}” is reachable but empty.",
                    "details": "No item exists to verify the metadata endpoint.",
                    "magnets": "This adapter does not extract infohashes from .torrent files.",
                },
            )
        reasons["magnets"] = (
            "The Internet Archive publishes .torrent files, but this adapter does not "
            "parse infohashes and does not treat them as verified magnets."
        )
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.VERIFIED,
            details=Capability.PROBABLE,
            artwork=Capability.PROBABLE,
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.PROBABLE,
            total_estimate=num_found,
            rate_limit="One page at a time with a delay; archive.org asks clients to be polite.",
            reasons=reasons,
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        try:
            page_number = int(cursor) if cursor not in (None, "") else 1
        except (TypeError, ValueError):
            raise SourceError(f"Unusable catalog cursor: {cursor!r}") from None
        if page_number < 1:
            raise SourceError(f"Unusable catalog cursor: {cursor!r}")
        limit = max(1, min(int(limit), 1000))
        url = self._search_url(page_number, limit)
        data = self._get(url)
        response = data.get("response")
        if not isinstance(response, Mapping):
            raise SourceError("The search response had no response object.")
        docs = response.get("docs")
        if not isinstance(docs, list):
            raise SourceError("The search response had no docs list.")
        total = _as_int(response.get("numFound"))
        games: list[SourceGame] = []
        skipped = 0
        for doc in docs:
            if not isinstance(doc, Mapping):
                skipped += 1
                continue
            game = self._game_from_doc(doc)
            if game is None:
                skipped += 1
                continue
            games.append(game)
        # Completion is decided by the source's own total when it gives one.
        complete = not games
        if total is not None and page_number * limit >= total:
            complete = True
        if total is None and len(docs) < limit:
            complete = True
        notes = (f"{skipped} record(s) on this page were not usable.",) if skipped else ()
        return SourcePage(
            games=games,
            next_cursor=None if complete else str(page_number + 1),
            total=total,
            page_number=page_number,
            url=url,
            notes=notes,
        )

    def _load_details(self, game_id: str) -> tuple[SourceGame, list[SourceMirror]]:
        validate_identifier(game_id)
        cached = self._details_cache.get(game_id)
        if cached is not None:
            return cached
        data = self._get(f"{ARCHIVE_BASE}/metadata/{game_id}")
        metadata = data.get("metadata")
        files = data.get("files")
        if not isinstance(metadata, Mapping):
            raise SourceError(f"{game_id} has no metadata object.")
        files = [f for f in files if isinstance(f, Mapping)] if isinstance(files, list) else []
        identifier = _as_text(metadata.get("identifier")).strip() or game_id
        title = meta.clean_text(_as_text(metadata.get("title")) or identifier, 300)
        game = SourceGame(
            source_id=self.id,
            game_id=identifier,
            title=title or identifier,
            year=meta.clean_year(_as_text(metadata.get("year")) or _as_text(metadata.get("date"))),
            creator=meta.clean_text(_as_text(metadata.get("creator"))),
            publisher=meta.clean_text(_as_text(metadata.get("publisher"))),
            release_date=meta.clean_release_date(_as_text(metadata.get("date"))),
            genres=_text_list(metadata.get("subject")),
            description=meta.clean_description(_as_text(metadata.get("description"))),
            cover_url=_poster_url(identifier, files),
            source_url=f"{ARCHIVE_BASE}/details/{identifier}",
            extra={"collection": self.collection},
        )
        mirrors: list[SourceMirror] = []
        seen: set[str] = set()
        for raw in files:
            name = _as_text(raw.get("name")).strip()
            if not name or raw.get("private") is True:
                continue
            format_name = _as_text(raw.get("format"))
            if is_auxiliary(name, format_name):
                continue
            if name.casefold().endswith(IMAGE_SUFFIXES):
                # Artwork is not a mirror of the game itself.
                continue
            url = f"{ARCHIVE_BASE}/download/{identifier}/{urllib.parse.quote(name)}"
            if url in seen:
                continue
            seen.add(url)
            algorithm, checksum = "", ""
            for field_name, label in (("sha256", "sha256"), ("sha1", "sha1"), ("md5", "md5")):
                value = _as_text(raw.get(field_name)).strip()
                if value:
                    algorithm, checksum = label, value
                    break
            mirrors.append(
                SourceMirror(
                    url=url,
                    filename=name,
                    size=_as_int(raw.get("size")),
                    format=format_name,
                    checksum_algorithm=algorithm,
                    checksum_value=checksum,
                    version=meta.clean_text(_as_text(metadata.get("version")), 40),
                )
            )
        result = (game, mirrors)
        if len(self._details_cache) >= 64:
            self._details_cache.clear()
        self._details_cache[game_id] = result
        return result

    def details(self, game_id: str) -> SourceGame:
        return self._load_details(game_id)[0]

    def mirrors(self, game_id: str) -> list[SourceMirror]:
        return list(self._load_details(game_id)[1])

    def magnets(self, game_id: str) -> list[Any]:
        raise UnsupportedOperation(
            "The Internet Archive adapter does not extract infohashes from .torrent files, "
            "so it reports no magnets."
        )


RAWG_API_BASE = "https://api.rawg.io/api"
RAWG_SITE = "https://rawg.io"


def _names(value: Any) -> tuple[str, ...]:
    """Collect display names from API objects like ``[{"name": "RPG"}]``."""
    names: list[str] = []
    if isinstance(value, Mapping):
        value = [value]
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("slug")
            else:
                name = item
            text = meta.clean_text(name, 80)
            if text:
                names.append(text)
    elif isinstance(value, str):
        names.append(value)
    return meta.clean_list(names)


def _platform_names(value: Any) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            platform = item.get("platform") if isinstance(item, Mapping) else item
            names.extend(_names(platform))
    return meta.clean_list(names)


class RawgSource(WebsiteSource):
    """RAWG's documented game metadata API (metadata only).

    Credentials are supplied by the caller (Settings or an environment variable
    in tests); they are never embedded in this module, written to the catalog,
    or included in error messages.
    """

    id = "rawg"
    name = "RAWG"
    canonical_url = f"{RAWG_SITE}/games"
    adapter_version = "1"
    hosts = ("api.rawg.io",)
    image_hosts = ("media.rawg.io", "rawg.io")
    site = RAWG_SITE
    response_format = "JSON API (results/count/next)"
    pagination_method = "page number (page=N, page_size<=40)"
    source_kind = "api"
    auth_kind = "API key"
    requires_auth = True
    download_capability = "store-links"
    permitted_reason = (
        "RAWG documents this public read-only API for game metadata; its terms "
        "require a user-supplied API key and attribution."
    )

    def __init__(
        self,
        api_key: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        fetch_json: Callable[..., dict[str, Any]] = request_json,
    ) -> None:
        super().__init__()
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout)
        self._fetch_json = fetch_json

    @classmethod
    def matches(cls, url: str) -> bool:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        return host in ("rawg.io", "www.rawg.io", "api.rawg.io")

    @classmethod
    def from_url(
        cls, url: str, *, api_keys: Mapping[str, str] | None = None
    ) -> "RawgSource":
        if not cls.matches(url):
            raise SourceError("That URL is not a RAWG URL.")
        key = ""
        if isinstance(api_keys, Mapping):
            key = str(api_keys.get("rawg") or "")
        key = key or __import__("os").environ.get("RAWG_API_KEY", "")
        return cls(key)

    def _redact(self, text: str) -> str:
        value = str(text)
        if self.api_key:
            value = value.replace(self.api_key, "***")
        return re.sub(r"([?&]key=)[^&\s]+", r"\1***", value)

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise SourceError(
                "RAWG needs an API key. Add one under Settings → Providers, or set "
                "the RAWG_API_KEY environment variable."
            )
        query = dict(params or {})
        query["key"] = self.api_key
        url = f"{RAWG_API_BASE}{path}?" + urllib.parse.urlencode(query)
        try:
            data = self._fetch_json(url, hosts=self.hosts, timeout=self.timeout)
        except AppError as exc:
            message = self._redact(str(exc))
            self.last_error = message
            # Keep the status and the provider's own words: a spent quota is
            # the difference between "this game has no match" and "no request
            # can succeed right now".
            raise SourceError(
                message,
                status=getattr(exc, "status", None),
                detail=str(getattr(exc, "detail", "") or ""),
            ) from exc
        except (OSError, ValueError) as exc:
            message = self._redact(f"{type(exc).__name__}: {exc}")
            self.last_error = message
            raise SourceError(f"RAWG could not be read: {message}") from exc
        if not isinstance(data, dict):
            raise SourceError("RAWG returned JSON that is not an object.")
        return data

    def _game_from_payload(self, payload: Mapping[str, Any]) -> SourceGame | None:
        game_id = _as_text(payload.get("id")).strip() or _as_text(payload.get("slug")).strip()
        title = meta.clean_text(_as_text(payload.get("name")) or _as_text(payload.get("slug")), 300)
        if not game_id or not title:
            return None
        slug = _as_text(payload.get("slug")).strip()
        source_url = f"{RAWG_SITE}/games/{urllib.parse.quote(slug)}" if slug else ""
        released = meta.clean_release_date(_as_text(payload.get("released")))
        stores: list[dict[str, str]] = []
        raw_stores = payload.get("stores")
        if isinstance(raw_stores, (list, tuple)):
            for item in raw_stores:
                if not isinstance(item, Mapping):
                    continue
                store = item.get("store")
                store_name = ""
                if isinstance(store, Mapping):
                    store_name = meta.clean_text(store.get("name") or store.get("slug"), 80)
                link = meta.clean_url(item.get("url") or (store.get("domain") if isinstance(store, Mapping) else ""))
                if store_name or link:
                    stores.append({"name": store_name, "url": link})
        extra: dict[str, Any] = {}
        for name, key in (
            ("rating", "rating"),
            ("metacritic", "metacritic"),
            ("playtime", "playtime"),
            ("ratings_count", "ratings_count"),
            ("added", "added"),
            ("slug", "slug"),
            ("website", "website"),
        ):
            value = payload.get(key)
            if isinstance(value, (int, float, str)) and value not in ("", None):
                extra[name] = value
        tags = _names(payload.get("tags"))
        if tags:
            extra["tags"] = list(tags)
        if stores:
            extra["stores"] = stores
        additional = meta.clean_url(payload.get("background_image_additional"))
        if additional:
            extra["background_image_additional"] = additional
        return SourceGame(
            source_id=self.id,
            game_id=game_id,
            title=title,
            year=meta.clean_year(_as_text(payload.get("released"))),
            developer=", ".join(_names(payload.get("developers"))),
            publisher=", ".join(_names(payload.get("publishers"))),
            release_date=released,
            genres=_names(payload.get("genres")),
            platforms=_platform_names(payload.get("platforms")),
            description=meta.clean_description(_as_text(payload.get("description_raw")) or _as_text(payload.get("description"))),
            cover_url=meta.clean_url(payload.get("background_image") or payload.get("background_image_additional")),
            source_url=source_url,
            extra=extra,
        )

    def inspect(self) -> CapabilityReport:
        if not self.api_key:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={
                    "catalog": "No RAWG API key is configured. Settings → Providers stores one securely; the app never embeds it in source code.",
                    "magnets": "RAWG is a metadata provider and publishes no magnets.",
                    "mirrors": "RAWG does not provide game files or mirror URLs.",
                },
            )
        try:
            data = self._get("/games", {"page": 1, "page_size": 1})
        except SourceError as exc:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={
                    "catalog": f"The RAWG API rejected the request: {exc}",
                    "magnets": "RAWG is a metadata provider and publishes no magnets.",
                    "mirrors": "RAWG does not provide game files or mirror URLs.",
                },
            )
        results = data.get("results")
        count = _as_int(data.get("count"))
        if not isinstance(results, list) or count is None:
            self.last_error = "The RAWG response did not contain results/count."
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={"catalog": self.last_error},
            )
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.VERIFIED,
            details=Capability.PROBABLE,
            artwork=Capability.PROBABLE,
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=count,
            rate_limit="RAWG asks clients to stay within the documented request quota; one page at a time is used.",
            reasons={
                "magnets": "RAWG is a metadata provider and publishes no magnets.",
                "mirrors": "RAWG does not provide game files or mirror URLs. Store links are metadata, not downloads.",
            },
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        try:
            page_number = int(cursor) if cursor not in (None, "") else 1
        except (TypeError, ValueError):
            raise SourceError(f"Unusable RAWG cursor: {cursor!r}") from None
        if page_number < 1:
            raise SourceError(f"Unusable RAWG cursor: {cursor!r}")
        limit = max(1, min(int(limit), 40))
        data = self._get("/games", {"page": page_number, "page_size": limit})
        results = data.get("results")
        if not isinstance(results, list):
            raise SourceError("The RAWG response had no results list.")
        count = _as_int(data.get("count"))
        games: list[SourceGame] = []
        skipped = 0
        for item in results:
            if not isinstance(item, Mapping):
                skipped += 1
                continue
            game = self._game_from_payload(item)
            if game is None:
                skipped += 1
                continue
            games.append(game)
        complete = not games
        if count is not None and page_number * limit >= count:
            complete = True
        if count is None and data.get("next") in (None, ""):
            complete = True
        notes = (f"{skipped} record(s) on this page were not usable.",) if skipped else ()
        return SourcePage(
            games=games,
            next_cursor=None if complete else str(page_number + 1),
            total=count,
            page_number=page_number,
            url=f"{RAWG_API_BASE}/games?page={page_number}&page_size={limit}",
            notes=notes,
        )

    def details(self, game_id: str) -> SourceGame:
        data = self._get(f"/games/{urllib.parse.quote(str(game_id))}")
        game = self._game_from_payload(data)
        if game is None:
            raise SourceError(f"RAWG returned no usable record for {game_id}.")
        return game

    def search_games(self, title: str, *, limit: int = 8) -> list[SourceGame]:
        """Search RAWG by name, using its documented ``search`` parameter.

        The results are ordinary records: a caller that wants artwork may use
        one, but a name is not an identity, so choosing is left to it.
        """
        name = " ".join(str(title or "").split())
        if not name:
            raise SourceError("A title is needed to search RAWG.")
        data = self._get(
            "/games",
            {"search": name[:120], "page_size": max(1, min(int(limit), 40))},
        )
        results = data.get("results")
        if not isinstance(results, list):
            raise SourceError("The RAWG search response had no results list.")
        games: list[SourceGame] = []
        for item in results:
            if not isinstance(item, Mapping):
                continue
            game = self._game_from_payload(item)
            if game is not None:
                games.append(game)
        return games

    def screenshots(self, game_id: str) -> tuple[str, ...]:
        """Gameplay screenshots from RAWG's documented screenshots endpoint."""
        data = self._get(f"/games/{urllib.parse.quote(str(game_id))}/screenshots")
        results = data.get("results")
        if not isinstance(results, list):
            return ()
        urls: list[str] = []
        for item in results:
            if not isinstance(item, Mapping):
                continue
            url = meta.clean_url(item.get("image"))
            if url and url not in urls:
                urls.append(url)
        return tuple(urls[:8])

    def mirrors(self, game_id: str) -> list[SourceMirror]:
        raise UnsupportedOperation(
            "RAWG is metadata only and does not publish game files or mirror URLs."
        )

    def magnets(self, game_id: str) -> list[Any]:
        raise UnsupportedOperation(
            "RAWG is metadata only and does not publish magnet links."
        )

    def test_connection(self) -> str:
        """Return the API's total game count, or raise a redacted SourceError."""
        data = self._get("/games", {"page": 1, "page_size": 1})
        count = _as_int(data.get("count"))
        if count is None:
            raise SourceError("RAWG answered, but not with the documented results/count shape.")
        return f"RAWG connection succeeded; it reports {count} game(s)."


def _kryoto_source_game(game: "kryoto.KryotoGame") -> SourceGame:
    """Map one verified Kryoto record onto the shared source model."""
    extra: dict[str, Any] = dict(game.extra)
    fields = dict(extra.get("source_fields") or {})
    # Keep the two sizes separate: an install size is not a download size.
    if game.size:
        fields["size"] = game.size
    if game.download_size:
        fields["download_size"] = game.download_size
    if game.version:
        fields["version"] = game.version
    if fields:
        extra["source_fields"] = fields
    extra["kryoto"] = {
        "slug": game.slug,
        "links": len(game.links),
        "detail_loaded": game.detail_loaded,
    }
    return SourceGame(
        source_id=KryotoSource.id,
        game_id=game.slug,
        title=game.title,
        year=game.year,
        developer=game.developer,
        genres=game.genres,
        platforms=game.platforms,
        description=game.description,
        cover_url=game.cover_url,
        source_url=game.source_url,
        extra=extra,
    )


def _kryoto_source_mirror(link: "kryoto.KryotoLink") -> SourceMirror:
    return SourceMirror(
        url=link.url,
        filename=link.name or link.display_label,
        size=link.size,
        checksum_algorithm="sha256" if link.sha256 else "",
        checksum_value=link.sha256,
        version=link.version,
        label=link.label or link.name,
        host=link.host,
        url_kind=link.kind,
        raw_url=link.raw_url,
        notes=("Versioned release " + link.release_id,) if link.release_id else (),
    )


_KRYOTO_POOL: ThreadPoolExecutor | None = None
_KRYOTO_POOL_LOCK = threading.Lock()


def _kryoto_page_pool() -> ThreadPoolExecutor:
    """The shared look-ahead pool for catalog pages.

    One pool is enough: a scan walks one source at a time, and sharing it keeps
    a dialog closed mid-scan from leaving a pool behind per source object.
    """
    global _KRYOTO_POOL
    with _KRYOTO_POOL_LOCK:
        if _KRYOTO_POOL is None:
            _KRYOTO_POOL = ThreadPoolExecutor(
                max_workers=kryoto.MAX_PREFETCH, thread_name_prefix="kryoto-pages"
            )
        return _KRYOTO_POOL


class KryotoSource(WebsiteSource):
    """The public catalog API of ``https://kryo.to``.

    Every route used here was verified against a live response: the catalog is
    offset-paginated, details are wrapped in ``game``, downloads carry links and
    checksums, and ``mirrors`` reports the site's own mirror-building jobs (not
    file URLs).  The API publishes no total, so this adapter never claims one.

    A record is *listed* when the catalog returns it, *linked* when it names
    source URLs, and *downloadable* only after a link check confirms a file
    response.  Those three states are deliberately separate.
    """

    id = "kryoto"
    name = "Kryoto"
    canonical_url = kryoto.KRYOTO_ORIGIN
    adapter_version = "1"
    hosts = kryoto.KRYOTO_HOSTS
    image_hosts = kryoto.KRYOTO_IMAGE_HOSTS
    site = kryoto.KRYOTO_ORIGIN
    response_format = (
        "JSON API (games[]; count is the returned page size, not a total)"
    )
    pagination_method = f"offset (offset=N, limit<= {kryoto.MAX_PAGE_LIMIT})"
    source_kind = "api"
    download_capability = "public-files"
    permitted_reason = (
        "Kryoto exposes this read-only catalog and its download metadata through "
        "the site's own public API; the application only reads those endpoints."
    )

    def __init__(
        self,
        *,
        timeout: float = kryoto.DEFAULT_TIMEOUT,
        client: "kryoto.KryotoClient | None" = None,
        cache_seconds: float = kryoto.DEFAULT_CACHE_SECONDS,
        prefetch: int = kryoto.DEFAULT_PREFETCH,
    ) -> None:
        super().__init__()
        self.timeout = float(timeout)
        self._client = client or kryoto.KryotoClient(
            timeout=self.timeout, cache_seconds=cache_seconds
        )
        self._details_cache: dict[str, SourceGame] = {}
        #: Pages fetched ahead of the scan.  Off by default so a caller (and a
        #: test with a scripted transport) sees exactly the requests it asked
        #: for; the application turns it on where scan speed matters.
        self.prefetch = max(0, min(int(prefetch), kryoto.MAX_PREFETCH))
        self._ahead: dict[int, Any] = {}
        self._inflight: dict[int, Future] = {}
        self._prefetch_lock = threading.Lock()
        self._exhausted_at: int | None = None
        self.prefetch_requests = 0

    @classmethod
    def from_url(
        cls, url: str, *, api_keys: Mapping[str, str] | None = None
    ) -> "KryotoSource":
        if not cls.matches(url):
            raise SourceError("That URL is not a Kryoto URL.")
        return cls(prefetch=kryoto.DEFAULT_PREFETCH)

    @property
    def client(self) -> "kryoto.KryotoClient":
        return self._client

    def _get(self, action: Callable[[], Any], what: str) -> Any:
        """Run one API call and convert its typed failure into a SourceError."""
        try:
            return action()
        except kryoto.KryotoError as exc:
            self.last_error = f"{what}: {exc}"
            raise SourceError(self.last_error) from exc

    # -- protocol -----------------------------------------------------------

    def inspect(self) -> CapabilityReport:
        reasons: dict[str, str] = {}
        try:
            page = self._client.list_games(0, 1, force=True)
        except kryoto.KryotoError as exc:
            self.last_error = str(exc)
            reasons["catalog"] = f"The catalog endpoint could not be read: {exc}"
            reasons["magnets"] = "The Kryoto API publishes no magnet links."
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNKNOWN,
                reasons=reasons,
            )
        reasons["catalog"] = (
            "The catalog answered with the documented games array. The API does "
            "not report a total; count is the size of the returned page."
        )
        reasons["magnets"] = "The Kryoto API publishes no magnet links."
        if not page.games:
            return CapabilityReport(
                catalog=Capability.VERIFIED,
                pagination=Capability.VERIFIED,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNKNOWN,
                total_estimate=None,
                reasons={
                    **reasons,
                    "details": "The catalog is empty, so details could not be checked.",
                    "artwork": "There is no record to check for artwork.",
                },
                checked_at=_now(),
            )
        first = page.games[0]
        try:
            detail = self._client.game(first.slug, force=True)
        except kryoto.KryotoError as exc:
            reasons["details"] = f"The detail endpoint could not be read: {exc}"
            details_state = Capability.UNKNOWN
            artwork_state = Capability.UNKNOWN
        else:
            reasons["details"] = (
                f"The detail endpoint returned the nested game object for {detail.slug}."
            )
            details_state = Capability.VERIFIED
            artwork_state = Capability.VERIFIED if detail.cover_url else Capability.PROBABLE
        try:
            downloads = self._client.downloads(first.slug, force=True)
        except kryoto.KryotoError as exc:
            reasons["mirrors"] = f"The downloads endpoint could not be read: {exc}"
            mirrors_state = Capability.UNKNOWN
        else:
            reasons["mirrors"] = (
                "The downloads endpoint returned links, checksums and versioned "
                "releases. The /mirrors endpoint reports the site's own mirror "
                "build jobs, not file URLs, and is never treated as one."
            )
            mirrors_state = Capability.VERIFIED if downloads.links else Capability.PROBABLE
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.VERIFIED,
            details=details_state,
            artwork=artwork_state,
            magnets=Capability.UNSUPPORTED,
            mirrors=mirrors_state,
            total_estimate=None,
            rate_limit=(
                "No published quota.  A scan keeps at most "
                f"{kryoto.DEFAULT_PREFETCH} page requests in flight because three "
                "or four measured 2-4x the throughput of one; 429/503 responses "
                "honour Retry-After and are retried a bounded number of times."
            ),
            reasons=reasons,
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        try:
            offset = int(cursor) if cursor not in (None, "") else 0
        except (TypeError, ValueError):
            raise SourceError(f"Unusable Kryoto cursor: {cursor!r}") from None
        if offset < 0:
            raise SourceError(f"Unusable Kryoto cursor: {cursor!r}")
        limit = max(1, min(int(limit), kryoto.MAX_PAGE_LIMIT))
        result = self._take_prefetched(offset)
        if isinstance(result, Exception):
            if isinstance(result, kryoto.KryotoError):
                self.last_error = f"The Kryoto catalog could not be read: {result}"
                raise SourceError(self.last_error) from result
            raise SourceError(str(result)) from result
        # Fill the pipeline first: the pages after this one load while this one
        # is still on the wire, so a scan does not pay full price per page.
        self._prefetch_from(offset + limit, limit)
        if result is None:
            result = self._get(
                lambda: self._client.list_games(offset, limit),
                "The Kryoto catalog could not be read",
            )
        games = [_kryoto_source_game(game) for game in result.games]
        # The list endpoint already returns full records.  Keep them so the
        # enrichment pass does not request the same game's detail again; a
        # direct detail read still goes to the API when it is not cached.
        for game, record in zip(result.games, games):
            if len(self._details_cache) >= 256:
                self._details_cache.clear()
            self._details_cache.setdefault(game.slug, record)
        return SourcePage(
            games=games,
            next_cursor=str(result.next_offset) if result.next_offset is not None else None,
            total=None,  # the API publishes no total; never invent one
            page_number=(offset // limit) + 1,
            url=result.url,
            notes=result.notes,
        )

    # -- look-ahead ---------------------------------------------------------

    def _take_prefetched(self, offset: int) -> Any:
        """Return a page already fetched for *offset*, waiting if necessary."""
        with self._prefetch_lock:
            ready = self._ahead.pop(offset, None)
            pending = self._inflight.pop(offset, None)
        if ready is not None:
            return ready
        if pending is not None:
            try:
                value = pending.result(timeout=self.timeout + 10.0)
            except Exception as exc:  # a failed page is reported as a page error
                return exc
            # The consumer took this page straight from the pool, so the stash
            # callback may not have run yet.  Record the end of the catalog
            # here too, or a scan that keeps up with prefetch never learns it.
            self._note_exhausted(offset, value)
            return value
        return None

    def _note_exhausted(self, offset: int, result: Any) -> None:
        """Remember a short page as the point where the catalog ends."""
        if not (isinstance(result, kryoto.KryotoPage) and result.complete):
            return
        end = offset + result.received
        with self._prefetch_lock:
            if self._exhausted_at is None or end < self._exhausted_at:
                self._exhausted_at = end

    def _prefetch_from(self, start: int, step: int) -> None:
        """Queue the next few pages so the scan does not idle on the network.

        Only full 25-record pages are worth guessing ahead: a caller walking a
        two-record page is probing, not scanning, and speculative requests there
        would cost the source more than they save.
        """
        if self.prefetch <= 0 or step < kryoto.MAX_PAGE_LIMIT:
            return
        pool = _kryoto_page_pool()
        for index in range(self.prefetch):
            offset = start + index * step
            with self._prefetch_lock:
                if self._exhausted_at is not None and offset >= self._exhausted_at:
                    break
                if offset in self._ahead or offset in self._inflight:
                    continue
                if len(self._ahead) + len(self._inflight) >= self.prefetch + 2:
                    break
                future = pool.submit(self._fetch_page, offset, step)
                self._inflight[offset] = future
                self.prefetch_requests += 1
            future.add_done_callback(
                lambda done, offset=offset: self._stash_page(offset, done)
            )

    def _fetch_page(self, offset: int, limit: int) -> Any:
        try:
            return self._client.list_games(offset, limit)
        except Exception as exc:  # re-raised by whoever consumes the page
            return exc

    def _stash_page(self, offset: int, future: Future) -> None:
        try:
            result = future.result()
        except Exception as exc:
            result = exc
        with self._prefetch_lock:
            self._inflight.pop(offset, None)
            self._ahead[offset] = result
            if isinstance(result, kryoto.KryotoPage) and result.complete:
                # A short page ends the catalog: nothing after it exists.
                end = offset + result.received
                if self._exhausted_at is None or end < self._exhausted_at:
                    self._exhausted_at = end

    def details(self, game_id: str) -> SourceGame:
        cached = self._details_cache.get(game_id)
        if cached is not None:
            return cached
        game = self._get(
            lambda: self._client.game(game_id),
            f"The Kryoto detail for {game_id} could not be read",
        )
        if not game.has_source_links:
            # The detail endpoint should carry links; the downloads endpoint is
            # authoritative for checksums but is not used to invent any here.
            pass
        record = _kryoto_source_game(game)
        if len(self._details_cache) >= 256:
            self._details_cache.clear()
        self._details_cache[game_id] = record
        return record

    def mirrors(self, game_id: str) -> list[SourceMirror]:
        try:
            downloads = self._client.downloads(game_id)
        except kryoto.KryotoError as exc:
            self.last_error = f"The Kryoto downloads for {game_id} could not be read: {exc}"
            if exc.kind != "not_found":
                raise SourceError(self.last_error) from exc
            # A record without a downloads document may still name links in its
            # detail object; those are real, so use them rather than fabricate.
            game = self._get(
                lambda: self._client.game(game_id),
                f"The Kryoto detail for {game_id} could not be read",
            )
            return [_kryoto_source_mirror(link) for link in game.links]
        return [_kryoto_source_mirror(link) for link in downloads.links]

    def magnets(self, game_id: str) -> list[Any]:
        raise UnsupportedOperation(
            "The Kryoto API publishes download links, not magnet URIs."
        )

    # -- extras -------------------------------------------------------------

    def check_link(self, url: str, *, allow_external: bool = False) -> "kryoto.LinkCheck":
        """Probe one link; never downloads its body and never assumes a file."""
        return kryoto.check_link(
            url, timeout=self.timeout, allow_external=allow_external
        )

    def search(self, query: str, limit: int = 10) -> list[SourceGame]:
        """The API's own search endpoint (lightweight results, no source links)."""
        results = self._get(
            lambda: self._client.search(query, limit),
            "The Kryoto search could not be read",
        )
        return [_kryoto_source_game(game) for game in results]


FREETOGAME_BASE = "https://www.freetogame.com/api"
FREETOGAME_SITE = "https://www.freetogame.com"
#: FreeToGame asks clients to stay at or below ten requests per second.
FREETOGAME_RATE_LIMIT = 10
FREETOGAME_INTERVAL = 1.0 / FREETOGAME_RATE_LIMIT
FREETOGAME_CACHE_SECONDS = 300.0
#: The documented ``platform`` *filter* values.  The records themselves spell the
#: same idea differently (``PC (Windows)`` in a list, ``Windows`` in details), so
#: the label is derived by :meth:`FreeToGameSource._platform_labels` instead.
FREETOGAME_PLATFORMS = {"pc": "Windows", "browser": "Browser"}
FREETOGAME_SORTS = ("release-date", "popularity", "alphabetical", "relevance")
_FREETOGAME_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9 -]{0,40}$")
_FREETOGAME_TAG = re.compile(r"^[a-z0-9][a-z0-9.+_-]{0,80}$")


class _Throttle:
    """Space requests to a fixed minimum interval, shared by one adapter."""

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.interval = max(0.0, float(interval))
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> float:
        """Sleep only as long as needed; return the seconds actually waited."""
        with self._lock:
            now = self._clock()
            delay = 0.0
            if self._last is not None and self.interval > 0:
                delay = self.interval - (now - self._last)
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
            self._last = now
            return max(0.0, delay)


class FreeToGameSource(WebsiteSource):
    """FreeToGame's documented public JSON API (metadata only).

    Only the documented endpoints are used: ``/games`` (optionally filtered by
    ``platform``, ``category``, ``sort-by``), ``/game?id=``, and ``/filter``
    (``tag``/``platform``).  FreeToGame publishes no pagination token, so one
    document is read per filter and no page parameter is invented.

    ``game_url`` is the game's official page, not an installer: it is stored as
    the record's source page and never as a mirror.  The API's ``platform`` is
    ``PC`` or ``Browser``; those are reported as Windows and Browser, never as
    Linux, because nothing here verifies a Linux build.
    """

    id = "freetogame"
    name = "FreeToGame"
    canonical_url = FREETOGAME_SITE + "/"
    adapter_version = "1"
    hosts = ("freetogame.com",)
    image_hosts = ("freetogame.com",)
    site = FREETOGAME_SITE
    response_format = "JSON API (array of games)"
    pagination_method = "none (one document per filter)"
    source_kind = "api"
    download_capability = "store-links"
    attribution = FREETOGAME_SITE + "/"
    attribution_label = "FreeToGame"
    permitted_reason = (
        "FreeToGame documents this public read-only JSON API for game metadata "
        "and asks for attribution."
    )

    def __init__(
        self,
        *,
        platform: str = "",
        category: str = "",
        sort_by: str = "",
        tag: str = "",
        search: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        cache_seconds: float = FREETOGAME_CACHE_SECONDS,
        min_interval: float = FREETOGAME_INTERVAL,
        fetch_json: Callable[..., Any] = request_json_document,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__()
        self._clock = clock or time.monotonic
        self._sleeper = sleep or time.sleep
        self.platform = self._platform(platform)
        self.category = self._category(category)
        self.sort_by = self._sort(sort_by)
        self.tag = self._tag(tag)
        self.search = meta.clean_text(search, 80)
        self.timeout = float(timeout)
        self.cache_seconds = max(0.0, float(cache_seconds))
        self._fetch_json = fetch_json
        self._throttle = _Throttle(
            float(min_interval), clock=self._clock, sleep=self._sleeper
        )
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- documented filter vocabulary ---------------------------------------

    @staticmethod
    def _platform(value: Any) -> str:
        text = _as_text(value).strip().casefold()
        if not text:
            return ""
        if text in FREETOGAME_PLATFORMS:
            return text
        if text == "all":
            return "all"
        raise SourceError(
            "FreeToGame's documented platforms are pc, browser, and all "
            f"(got {value!r})."
        )

    @staticmethod
    def _category(value: Any) -> str:
        text = _as_text(value).strip().casefold()
        if not text:
            return ""
        if not _FREETOGAME_CATEGORY.match(text):
            raise SourceError(f"That is not a FreeToGame category: {value!r}")
        return text

    @staticmethod
    def _sort(value: Any) -> str:
        text = _as_text(value).strip().casefold()
        if not text:
            return ""
        if text not in FREETOGAME_SORTS:
            raise SourceError(
                "FreeToGame's documented sort values are "
                f"{', '.join(FREETOGAME_SORTS)} (got {value!r})."
            )
        return text

    @staticmethod
    def _tag(value: Any) -> str:
        text = _as_text(value).strip().casefold()
        if not text:
            return ""
        if not _FREETOGAME_TAG.match(text):
            raise SourceError(f"That is not a FreeToGame tag: {value!r}")
        return text

    @classmethod
    def from_url(
        cls, url: str, *, api_keys: Mapping[str, str] | None = None
    ) -> "FreeToGameSource":
        """Build the adapter from a URL, reading only documented query keys."""
        if not cls.matches(url):
            raise SourceError("That URL is not a FreeToGame URL.")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)

        def first(*names: str) -> str:
            for name in names:
                values = query.get(name)
                if values and values[0].strip():
                    return values[0].strip()
            return ""

        return cls(
            platform=first("platform"),
            category=first("category"),
            sort_by=first("sort-by", "sort_by"),
            tag=first("tag"),
        )

    # -- request plumbing ---------------------------------------------------

    def request_url(self) -> str:
        """The documented endpoint URL for this adapter's filters."""
        if self.tag:
            params: dict[str, str] = {"tag": self.tag}
            if self.platform:
                params["platform"] = self.platform
            return f"{FREETOGAME_BASE}/filter?" + urllib.parse.urlencode(params)
        params = {}
        if self.platform:
            params["platform"] = self.platform
        if self.category:
            params["category"] = self.category
        if self.sort_by:
            params["sort-by"] = self.sort_by
        base = f"{FREETOGAME_BASE}/games"
        return f"{base}?{urllib.parse.urlencode(params)}" if params else base

    @property
    def canonical_url(self) -> str:  # type: ignore[override]
        return self.request_url()

    def clear_cache(self) -> None:
        """Forget cached responses so the next call talks to the API again."""
        self._cache.clear()

    def _get(self, url: str) -> Any:
        """Return one document, reusing a recent response instead of refetching."""
        cached = self._cache.get(url)
        if cached is not None and self._clock() - cached[0] <= self.cache_seconds:
            return cached[1]
        self._throttle.wait()
        try:
            data = self._fetch_json(url, hosts=self.hosts, timeout=self.timeout)
        except AppError as exc:
            self.last_error = str(exc)
            raise SourceError(f"FreeToGame could not be read: {exc}") from exc
        except (OSError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise SourceError(f"FreeToGame could not be read: {self.last_error}") from exc
        self._cache[url] = (self._clock(), data)
        return data

    # -- normalization ------------------------------------------------------

    @staticmethod
    def _platform_labels(value: Any) -> tuple[str, ...]:
        """The honest platform label for an API value, or ``()`` when unknown.

        The API writes ``PC (Windows)`` in the list endpoint and ``Windows`` in
        the details endpoint; both mean Windows.  A browser game stays Browser,
        and an unfamiliar label is left unlabelled rather than guessed - nothing
        here ever produces "Linux" from a Windows value.
        """
        text = _as_text(value).strip().casefold()
        if not text:
            return ()
        if "browser" in text:
            return ("Browser",)
        if "windows" in text or text == "pc":
            return ("Windows",)
        return ()

    def _game_from_payload(self, payload: Mapping[str, Any]) -> SourceGame | None:
        game_id = _as_text(payload.get("id")).strip()
        title = meta.clean_text(_as_text(payload.get("title")), 300)
        if not game_id or not title:
            return None
        release_date = meta.clean_release_date(_as_text(payload.get("release_date")))
        genre = meta.clean_text(_as_text(payload.get("genre")), 80)
        extra: dict[str, Any] = {}
        profile_url = meta.clean_url(payload.get("freetogame_profile_url"))
        if profile_url:
            extra["profile_url"] = profile_url
        platform_text = meta.clean_text(_as_text(payload.get("platform")), 40)
        if platform_text:
            extra["platform"] = platform_text
        status = meta.clean_text(_as_text(payload.get("status")), 40)
        if status:
            extra["status"] = status
        return SourceGame(
            source_id=self.id,
            game_id=game_id,
            title=title,
            year=meta.clean_year(release_date),
            developer=meta.clean_text(_as_text(payload.get("developer")), 160),
            publisher=meta.clean_text(_as_text(payload.get("publisher")), 160),
            release_date=release_date,
            genres=(genre,) if genre else (),
            platforms=self._platform_labels(payload.get("platform")),
            description=meta.clean_description(
                _as_text(payload.get("short_description"))
            ),
            cover_url=meta.clean_url(payload.get("thumbnail")),
            source_url=meta.clean_url(payload.get("game_url")),
            extra=extra,
        )

    def _matches_search(self, game: SourceGame) -> bool:
        if not self.search:
            return True
        haystack = " ".join(
            (game.title, game.description, game.developer, game.publisher, *game.genres)
        ).casefold()
        return self.search.casefold() in haystack

    # -- operations ---------------------------------------------------------

    def inspect(self) -> CapabilityReport:
        reasons = {
            "pagination": (
                "FreeToGame returns the whole filtered list in one document and "
                "documents no page parameter."
            ),
            "details": (
                "The /game?id= endpoint adds the long description, status, "
                "screenshots, and minimum system requirements."
            ),
            "artwork": "Thumbnails and screenshots are the API's own image URLs.",
            "magnets": "FreeToGame is a metadata directory and publishes no magnets.",
            "mirrors": (
                "FreeToGame links to the game's official page, never to an installer."
            ),
        }
        try:
            page = self.page(None, 50)
        except SourceError as exc:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNKNOWN,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={**reasons, "catalog": str(exc)},
            )
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.UNSUPPORTED,
            details=Capability.PROBABLE,
            artwork=(
                Capability.PROBABLE
                if any(game.cover_url for game in page.games)
                else Capability.UNKNOWN
            ),
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=page.total,
            rate_limit=(
                "FreeToGame asks clients to stay at or below 10 requests per second; "
                "requests are spaced and responses cached for "
                f"{int(self.cache_seconds)} s."
            ),
            reasons={
                **reasons,
                "catalog": (
                    f"{len(page.games)} record(s) read from the documented endpoint "
                    f"{self.request_url()}."
                ),
            },
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        if cursor not in (None, ""):
            raise SourceError(
                "FreeToGame documents no pagination; one document holds the whole "
                "filtered list."
            )
        url = self.request_url()
        data = self._get(url)
        if not isinstance(data, list):
            raise SourceError(
                "FreeToGame returned JSON that is not a list of games."
            )
        games: list[SourceGame] = []
        skipped = 0
        for item in data:
            if not isinstance(item, Mapping):
                skipped += 1
                continue
            game = self._game_from_payload(item)
            if game is None:
                skipped += 1
                continue
            if self._matches_search(game):
                games.append(game)
        notes: tuple[str, ...] = ()
        if skipped:
            notes = (f"{skipped} record(s) in the response had no usable id or title.",)
        return SourcePage(
            games=games,
            next_cursor=None,
            total=len(games),
            page_number=1,
            url=url,
            notes=notes,
        )

    def details(self, game_id: str) -> SourceGame:
        key = _as_text(game_id).strip()
        if not key.isdigit():
            raise SourceError("FreeToGame game ids are numbers.")
        data = self._get(
            f"{FREETOGAME_BASE}/game?id=" + urllib.parse.quote(key)
        )
        if not isinstance(data, Mapping):
            raise SourceError("FreeToGame returned details that are not an object.")
        game = self._game_from_payload(data)
        if game is None:
            raise SourceError(f"FreeToGame returned no usable record for {key}.")
        description = meta.clean_description(_as_text(data.get("description")))
        if description:
            game.description = description
        requirements = data.get("minimum_system_requirements")
        if isinstance(requirements, Mapping):
            kept = {
                name: meta.clean_text(requirements.get(name), 120)
                for name in ("os", "processor", "memory", "graphics", "storage")
                if requirements.get(name)
            }
            if kept:
                game.extra["minimum_system_requirements"] = kept
        screenshots = data.get("screenshots")
        if isinstance(screenshots, list):
            images = [
                meta.clean_url(item.get("image"))
                for item in screenshots
                if isinstance(item, Mapping)
            ]
            images = [url for url in images if url]
            if images:
                game.extra["screenshots"] = images[:8]
        return game

    def mirrors(self, game_id: str) -> list[SourceMirror]:
        raise UnsupportedOperation(
            "FreeToGame publishes the game's official page, not an installer or "
            "mirror URL; open that page to install."
        )

    def magnets(self, game_id: str) -> list[Any]:
        raise UnsupportedOperation(
            "FreeToGame is a metadata directory and publishes no magnet links."
        )


#: Canonical fields a user mapping may address.
CONFIG_FIELDS = (
    "id", "title", "description", "release_date", "year", "developer",
    "publisher", "genres", "platforms", "cover_url", "source_url", "tags",
    "rating",
)
MAX_CONFIG_KEY = 80


def _config_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise SourceError(f"{label} must be a dotted JSON path.")
    text = value.strip().strip(".")
    if not text:
        return ""
    if len(text) > 200 or not re.fullmatch(r"[A-Za-z0-9_.\[\]-]+", text):
        raise SourceError(f"{label} is not a usable JSON path: {value!r}.")
    return text


def normalize_source_config(raw: Any) -> dict[str, Any]:
    """Validate a user's catalog mapping, returning only known settings.

    Accepted shape (every key optional)::

        {"records": "games",
         "fields": {"id": "productId", "source_url": "pageUrl"},
         "pagination": {"type": "page", "param": "page",
                        "size_param": "limit", "start": 1,
                        "total_path": "count", "next_path": "next"}}

    Nothing is guessed: an unknown field name or pagination kind is refused with
    the reason, so a typo cannot silently import the wrong column.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise SourceError(f"The mapping is not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise SourceError("The mapping must be a JSON object.")
    config: dict[str, Any] = {}

    records = raw.get("records")
    if records not in (None, ""):
        config["records"] = _config_path(records, label="records")

    fields = raw.get("fields")
    if fields is not None:
        if not isinstance(fields, Mapping):
            raise SourceError("fields must be an object of field name to source key.")
        cleaned: dict[str, str] = {}
        for name, key in fields.items():
            canonical = str(name).strip()
            if canonical not in CONFIG_FIELDS:
                raise SourceError(
                    f"Unknown field {canonical!r}. Use one of: "
                    + ", ".join(CONFIG_FIELDS)
                    + "."
                )
            if not isinstance(key, str) or not key.strip():
                raise SourceError(f"The mapping for {canonical!r} must be a key name.")
            if len(key) > MAX_CONFIG_KEY:
                raise SourceError(f"The mapping for {canonical!r} is too long.")
            cleaned[canonical] = key.strip()
        if cleaned:
            config["fields"] = cleaned

    pagination = raw.get("pagination")
    if pagination not in (None, {}):
        if not isinstance(pagination, Mapping):
            raise SourceError("pagination must be an object.")
        kind = str(pagination.get("type") or "none").strip().casefold()
        if kind not in ("none", "page", "offset", "cursor"):
            raise SourceError(
                "pagination.type must be one of: none, page, offset, cursor."
            )
        settings: dict[str, Any] = {"type": kind}
        if kind != "none":
            default_param = {"page": "page", "offset": "offset", "cursor": ""}[kind]
            param = pagination.get("param", default_param)
            if param or kind != "cursor":
                if not isinstance(param, str) or not re.fullmatch(
                    r"[A-Za-z0-9_.-]{1,40}", param
                ):
                    raise SourceError("pagination.param must be a query parameter name.")
                settings["param"] = param
            size_param = pagination.get("size_param")
            if size_param is not None:
                if not isinstance(size_param, str) or not re.fullmatch(
                    r"[A-Za-z0-9_.-]{1,40}", size_param
                ):
                    raise SourceError(
                        "pagination.size_param must be a query parameter name."
                    )
                settings["size_param"] = size_param
            start = pagination.get("start")
            if start is not None:
                if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 1_000_000:
                    raise SourceError("pagination.start must be a whole number.")
                settings["start"] = start
            for key, label in (("total_path", "total_path"), ("next_path", "next_path")):
                if pagination.get(key) not in (None, ""):
                    settings[label] = _config_path(pagination[key], label=f"pagination.{label}")
        config["pagination"] = settings
    return config


def _dig(data: Any, path: str) -> Any:
    """Follow a dotted path (``a.b.0.c``) through mappings and lists."""
    current = data
    for part in [p for p in path.split(".") if p]:
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _fetch_json_document(url: str, *, name: str, fetcher=None):
    """Fetch a JSON document through the public-address/robots policy."""
    from .catalog import CatalogError

    active = fetcher or _cached_fetcher(url, name)
    try:
        response = active.fetch(url)
    except CatalogError as exc:
        raise SourceError(f"The JSON catalog could not be read: {exc}") from exc
    if response.status >= 400:
        raise SourceError(f"The JSON catalog answered HTTP {response.status}.")
    content_type = response.content_type
    if (
        content_type
        and "json" not in content_type
        and not url.lower().endswith(JSON_CATALOG_SUFFIXES)
    ):
        raise SourceError(f"The catalog answered {content_type}, not JSON.")
    try:
        data = json.loads(response.body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise SourceError("The catalog did not return valid JSON.") from exc
    if not isinstance(data, (dict, list)):
        raise SourceError("The catalog JSON is not an object or a list.")
    return data


class ConfiguredJsonSource(WebsiteSource):
    """Any public JSON endpoint, read through a mapping the user supplies.

    This is the configurable adapter: base URL, records path, field names, and
    pagination are explicit configuration rather than guesses about a site.  It
    is metadata only — it never derives a download URL from a record.
    """

    name = "Configured JSON catalog"
    adapter_version = "1"
    response_format = "JSON with a user-supplied mapping"
    source_kind = "catalog"
    download_capability = "none"
    permitted_reason = (
        "The user configured this public JSON endpoint and its field mapping "
        "themselves; robots.txt and public-address checks still apply."
    )

    def __init__(self, url: str, config: Any, *, fetcher=None) -> None:
        super().__init__()
        split = urllib.parse.urlsplit(url)
        host = (split.hostname or "").lower()
        if split.scheme != "https" or not host:
            raise SourceError("A configured catalog must be an HTTPS URL.")
        self.config = normalize_source_config(config)
        fingerprint = url + json.dumps(self.config, sort_keys=True)
        self.id = "cfg:" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
        self.canonical_url = url
        self.hosts = (host,)
        self.name = f"JSON catalog — {host}"
        self._fetcher = fetcher
        self._known: dict[str, SourceGame] = {}
        self.image_hosts: tuple[str, ...] = ()
        settings = self.config.get("pagination", {})
        kind = settings.get("type", "none")
        self.pagination_method = {
            "none": "single document",
            "page": "page number",
            "offset": "record offset",
            "cursor": "next link",
        }.get(kind, "single document")
        if settings.get("param"):
            self.pagination_method += f" ({settings['param']})"

    # -- reading ------------------------------------------------------------

    def _items(self, data: Any) -> list[Mapping[str, Any]]:
        path = self.config.get("records", "")
        node = _dig(data, path) if path else data
        if isinstance(node, Mapping):
            for key in ("games", "results", "items", "data", "records"):
                if isinstance(node.get(key), list):
                    node = node[key]
                    break
        if not isinstance(node, list):
            where = f"records path {path!r}" if path else "the document root"
            raise SourceError(f"{where} did not lead to a list of records.")
        return [item for item in node if isinstance(item, Mapping)]

    def _remember(self, games: Sequence[SourceGame]) -> None:
        from .feeds import _image_hosts

        for game in games:
            self._known.setdefault(game.game_id, game)
        hosts = list(self.image_hosts)
        for host in _image_hosts(games):
            if host not in hosts and len(hosts) < 12:
                hosts.append(host)
        self.image_hosts = tuple(hosts)

    def _games(self, data: Any) -> list[SourceGame]:
        from .feeds import record_from_mapping

        games: list[SourceGame] = []
        field_map = self.config.get("fields", {})
        for raw in self._items(data):
            game = record_from_mapping(
                raw,
                source_id=self.id,
                fallback_url=self.canonical_url,
                field_map=field_map,
            )
            if game is not None:
                games.append(game)
        self._remember(games)
        return games

    def _page_url(self, cursor: str | None, limit: int) -> tuple[str, int | None]:
        """Return the URL to request and the numeric cursor it encodes."""
        settings = self.config.get("pagination", {})
        kind = settings.get("type", "none")
        if kind == "none":
            return self.canonical_url, None
        if kind == "cursor":
            return (cursor or self.canonical_url), None
        try:
            value = int(cursor) if cursor not in (None, "") else int(
                settings.get("start", 1 if kind == "page" else 0)
            )
        except (TypeError, ValueError):
            raise SourceError(f"Unusable cursor for this catalog: {cursor!r}") from None
        if value < 0:
            raise SourceError(f"Unusable cursor for this catalog: {cursor!r}")
        params: dict[str, Any] = {settings.get("param", kind): value}
        if settings.get("size_param"):
            params[settings["size_param"]] = limit
        split = urllib.parse.urlsplit(self.canonical_url)
        query = dict(urllib.parse.parse_qsl(split.query, keep_blank_values=True))
        query.update({key: str(val) for key, val in params.items()})
        url = urllib.parse.urlunsplit(
            (split.scheme, split.netloc, split.path, urllib.parse.urlencode(query), "")
        )
        return url, value

    def inspect(self) -> CapabilityReport:
        try:
            page = self.page(None, 50)
        except SourceError as exc:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNSUPPORTED,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={"catalog": str(exc)},
            )
        with_page = sum(1 for game in page.games if self._has_own_page(game))
        return CapabilityReport(
            catalog=Capability.VERIFIED if page.games else Capability.UNKNOWN,
            pagination=(
                Capability.VERIFIED if page.next_cursor else
                Capability.UNSUPPORTED if self.pagination_method.startswith("single")
                else Capability.PROBABLE
            ),
            details=Capability.PROBABLE if with_page else Capability.UNSUPPORTED,
            artwork=Capability.PROBABLE,
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=page.total,
            reasons={
                "catalog": "" if page.games else (
                    "The mapping produced no record with a title."
                ),
                "pagination": (
                    "" if page.next_cursor else
                    "No further page was reported by this configuration."
                ),
                "details": (
                    f"{with_page} record(s) name their own HTTPS page; the details "
                    "pass reads that page's published JSON-LD or Open Graph metadata."
                    if with_page else
                    "No record names its own page, so there is nothing to open."
                ),
                "magnets": "A configured catalog never invents magnets.",
                "mirrors": "A configured catalog never invents download URLs.",
            },
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        settings = self.config.get("pagination", {})
        kind = settings.get("type", "none")
        limit = max(1, min(int(limit), 1000))
        url, value = self._page_url(cursor, limit)
        data = _fetch_json_document(url, name=self.name, fetcher=self._fetcher)
        games = self._games(data)
        total = None
        if settings.get("total_path"):
            raw_total = _dig(data, settings["total_path"])
            if isinstance(raw_total, int) and not isinstance(raw_total, bool) and raw_total >= 0:
                total = raw_total
        next_cursor: str | None = None
        if kind == "cursor":
            target = _dig(data, settings.get("next_path", "next"))
            if isinstance(target, Mapping):
                target = target.get("url") or target.get("href")
            if isinstance(target, str) and target.strip():
                candidate = urllib.parse.urljoin(url, target.strip())
                if urllib.parse.urlsplit(candidate).scheme == "https":
                    next_cursor = candidate
        elif kind == "page" and value is not None:
            if total is not None:
                next_cursor = str(value + 1) if value * limit < total else None
            elif len(games) >= limit:
                next_cursor = str(value + 1)
        elif kind == "offset" and value is not None:
            consumed = value + len(games)
            if total is not None:
                next_cursor = str(consumed) if consumed < total else None
            elif len(games) >= limit:
                next_cursor = str(consumed)
        return SourcePage(
            games=games,
            next_cursor=next_cursor,
            total=total,
            page_number=(value + 1) if isinstance(value, int) else 1,
            url=url,
        )

    def _has_own_page(self, game: SourceGame) -> bool:
        url = game.source_url or ""
        if url == self.canonical_url:
            return False
        return urllib.parse.urlsplit(url).scheme == "https"

    def details(self, game_id: str) -> SourceGame:
        game = self._known.get(str(game_id))
        if game is None:
            raise SourceError(f"{game_id} was not part of this catalog.")
        if not self._has_own_page(game):
            raise UnsupportedOperation(
                "This record has no HTTPS source page to read details from."
            )
        found = page_metadata(
            game.source_url,
            source_id=self.id,
            game_id=game.game_id,
            fetcher=self._fetcher,
        )
        found.title = game.title or found.title
        found.source_url = game.source_url
        return found

    def describe(self) -> dict[str, Any]:
        described = super().describe()
        described["config"] = dict(self.config)
        return described


SITEMAP_NAMES = (
    "sitemap.xml", "sitemap_index.xml", "sitemap-index.xml",
)
JSON_CATALOG_SUFFIXES = (".json", ".jsonl", ".ndjson")
MAX_SITEMAP_ENTRIES = 5000
MAX_SITEMAP_FILES = 20


def _public_policy(url: str, *, name: str, max_bytes: int = MAX_JSON_BYTES):
    from .catalog import SourcePolicy

    split = urllib.parse.urlsplit(url)
    host = (split.hostname or "").lower()
    if split.scheme != "https" or not host:
        raise SourceError("Only public HTTPS catalog URLs are supported here.")
    return SourcePolicy(
        name=name,
        hosts=frozenset({host}),
        permitted_reason=(
            "The user explicitly entered this public catalog URL and asked the "
            "application to read its published entries."
        ),
        timeout=DEFAULT_TIMEOUT,
        max_bytes=max_bytes,
        min_delay=1.0,
    )


def _source_key(prefix: str, url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "catalog").lower()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{host}:{digest}"


class SitemapSource(WebsiteSource):
    """A public XML sitemap or sitemap index, read through the policy fetcher.

    Sitemaps contain links, not game metadata: titles are derived from the last
    path segment and no description, date, platform, or download URL is invented.
    """

    name = "Public sitemap"
    adapter_version = "1"
    response_format = "XML sitemap"
    pagination_method = "entry offset across sitemap files"
    source_kind = "sitemap"
    download_capability = "none"
    permitted_reason = "A sitemap is published for crawlers; only its links are read."

    def __init__(self, url: str, *, fetcher=None) -> None:
        super().__init__()
        split = urllib.parse.urlsplit(url)
        host = (split.hostname or "").lower()
        if split.scheme != "https" or not host:
            raise SourceError("A sitemap source must be an HTTPS URL.")
        self.id = _source_key("sitemap", url)
        self.name = f"Sitemap — {host}"
        self.canonical_url = url
        self.hosts = (host,)
        self._fetcher = fetcher
        self._entries_cache: list[SourceGame] | None = None

    def _policy(self):
        return _public_policy(self.canonical_url, name=self.name)

    def _entries(self) -> list[SourceGame]:
        if self._entries_cache is not None:
            return self._entries_cache
        from .catalog import CatalogError, SitemapCatalogAdapter, build_fetcher

        policy = self._policy()
        adapter = SitemapCatalogAdapter(
            policy,
            self.canonical_url,
            fetcher=self._fetcher or build_fetcher(policy),
            max_entries=MAX_SITEMAP_ENTRIES,
            max_sitemaps=MAX_SITEMAP_FILES,
        )
        try:
            entries = adapter.list_entries()
        except CatalogError as exc:
            self.last_error = str(exc)
            raise SourceError(f"The sitemap could not be read: {exc}") from exc
        games: list[SourceGame] = []
        for entry in entries:
            games.append(
                SourceGame(
                    source_id=self.id,
                    game_id=entry.identifier,
                    title=entry.title,
                    source_url=entry.page_url,
                    extra={"sitemap": self.canonical_url},
                )
            )
        self._entries_cache = games
        return games

    def inspect(self) -> CapabilityReport:
        try:
            games = self._entries()
        except SourceError as exc:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNSUPPORTED,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={"catalog": str(exc)},
            )
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.VERIFIED,
            details=Capability.UNSUPPORTED,
            artwork=Capability.UNKNOWN,
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=len(games),
            reasons={
                "details": "A sitemap contains links, not game metadata.",
                "artwork": "A sitemap entry has no artwork field.",
                "magnets": "A sitemap never supplies magnets.",
                "mirrors": "A sitemap never supplies download URLs.",
            },
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        try:
            offset = int(cursor) if cursor not in (None, "") else 0
        except (TypeError, ValueError):
            raise SourceError(f"Unusable sitemap cursor: {cursor!r}") from None
        if offset < 0:
            raise SourceError(f"Unusable sitemap cursor: {cursor!r}")
        games = self._entries()
        limit = max(1, min(int(limit), 1000))
        chunk = games[offset:offset + limit]
        next_offset = offset + len(chunk)
        complete = next_offset >= len(games)
        return SourcePage(
            games=list(chunk),
            next_cursor=None if complete else str(next_offset),
            total=len(games),
            page_number=offset // limit + 1,
            # Distinct per page: a repeated URL means "this source stopped
            # advancing" to the importer, which is not the case here.
            url=f"{self.canonical_url}#offset={offset}",
        )

    def details(self, game_id: str) -> SourceGame:
        raise UnsupportedOperation("A sitemap has no detail metadata.")


class JsonCatalogSource(WebsiteSource):
    """A public JSON catalog using the same tolerant alias mapping as CSV import."""

    name = "Public JSON catalog"
    adapter_version = "1"
    response_format = "JSON catalog (array/results/items)"
    pagination_method = "next URL when present; otherwise one page"
    source_kind = "catalog"
    download_capability = "none"
    permitted_reason = "The user explicitly entered this public JSON catalog URL."

    def __init__(self, url: str, *, fetcher=None) -> None:
        super().__init__()
        split = urllib.parse.urlsplit(url)
        host = (split.hostname or "").lower()
        if split.scheme != "https" or not host:
            raise SourceError("A JSON catalog must be an HTTPS URL.")
        self.id = _source_key("json", url)
        self.name = f"JSON catalog — {host}"
        self.canonical_url = url
        self.hosts = (host,)
        self._fetcher = fetcher
        #: Records seen while paging, so the details pass can find each one's
        #: own page without downloading the whole document again.
        self._known: dict[str, SourceGame] = {}
        self.image_hosts: tuple[str, ...] = ()

    def _remember(self, games: Sequence[SourceGame]) -> None:
        from .feeds import _image_hosts

        for game in games:
            self._known.setdefault(game.game_id, game)
        hosts = list(self.image_hosts)
        for host in _image_hosts(games):
            if host not in hosts and len(hosts) < 12:
                hosts.append(host)
        self.image_hosts = tuple(hosts)

    def _get(self, url: str) -> tuple[dict[str, Any] | list[Any], str]:
        from .catalog import CatalogError, build_fetcher

        policy = _public_policy(self.canonical_url, name=self.name)
        fetcher = self._fetcher or build_fetcher(policy)
        try:
            response = fetcher.fetch(url)
        except CatalogError as exc:
            self.last_error = str(exc)
            raise SourceError(f"The JSON catalog could not be read: {exc}") from exc
        if response.status >= 400:
            raise SourceError(f"The JSON catalog answered HTTP {response.status}.")
        content_type = response.content_type
        if content_type and "json" not in content_type and not url.lower().endswith(JSON_CATALOG_SUFFIXES):
            raise SourceError(
                f"The catalog answered {content_type}, not JSON."
            )
        try:
            data = json.loads(response.body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise SourceError("The catalog did not return valid JSON.") from exc
        if not isinstance(data, (dict, list)):
            raise SourceError("The catalog JSON is not an object or a list.")
        return data, url

    @staticmethod
    def _next_url(data: Any, current: str) -> str | None:
        if not isinstance(data, Mapping):
            return None
        value: Any = None
        for key in ("next", "next_url", "next_page", "nextPage"):
            if data.get(key) not in (None, "", False):
                value = data.get(key)
                break
        links = data.get("links")
        if value is None and isinstance(links, Mapping):
            value = links.get("next")
        if isinstance(value, Mapping):
            value = value.get("url") or value.get("href")
        if not isinstance(value, str) or not value.strip():
            return None
        target = urllib.parse.urljoin(current, value.strip())
        split = urllib.parse.urlsplit(target)
        if split.scheme != "https" or not split.hostname:
            return None
        return target

    @staticmethod
    def _total(data: Any) -> int | None:
        if not isinstance(data, Mapping):
            return None
        for key in ("count", "total", "total_count", "totalCount"):
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    def _games(self, data: Any) -> list[SourceGame]:
        from .feeds import items_from_json, record_from_mapping

        games: list[SourceGame] = []
        for raw in items_from_json(data):
            game = record_from_mapping(
                raw, source_id=self.id, fallback_url=self.canonical_url
            )
            if game is not None:
                games.append(game)
        self._remember(games)
        return games

    def inspect(self) -> CapabilityReport:
        try:
            data, _url = self._get(self.canonical_url)
            games = self._games(data)
        except SourceError as exc:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNKNOWN,
                details=Capability.UNSUPPORTED,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={"catalog": str(exc)},
            )
        next_url = self._next_url(data, self.canonical_url)
        total = self._total(data) or len(games)
        with_page = sum(1 for game in games if self._has_own_page(game))
        return CapabilityReport(
            catalog=Capability.VERIFIED if games else Capability.UNKNOWN,
            pagination=Capability.VERIFIED if next_url else Capability.UNSUPPORTED,
            details=Capability.PROBABLE if with_page else Capability.UNSUPPORTED,
            artwork=Capability.PROBABLE,
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=total if games else None,
            reasons={
                "catalog": "" if games else "The JSON contained no record with a title.",
                "pagination": "" if next_url else "No next URL was present in the JSON.",
                "details": (
                    f"{with_page} record(s) on the first page carry their own HTTPS "
                    "page; the details pass reads that page's published JSON-LD or "
                    "Open Graph metadata."
                    if with_page else
                    "No record on the first page carries a source page URL, so "
                    "there is nothing to open for details."
                ),
                "magnets": "A JSON catalog never invents magnets.",
                "mirrors": "A JSON catalog never invents download URLs.",
            },
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        url = cursor or self.canonical_url
        data, _url = self._get(url)
        games = self._games(data)
        next_url = self._next_url(data, url)
        total = self._total(data)
        return SourcePage(
            games=games,
            next_cursor=next_url,
            total=total,
            page_number=1,
            url=url,
        )

    def _has_own_page(self, game: SourceGame) -> bool:
        """True only when the record names a page other than the catalog itself."""
        url = game.source_url or ""
        if url == self.canonical_url:
            return False
        return urllib.parse.urlsplit(url).scheme == "https"

    def details(self, game_id: str) -> SourceGame:
        """Read the record's own page, like a local catalog's details pass."""
        game = self._known.get(str(game_id))
        if game is None:
            raise SourceError(f"{game_id} was not part of this catalog.")
        if not self._has_own_page(game):
            raise UnsupportedOperation(
                "This record has no HTTPS source page to read details from."
            )
        found = page_metadata(
            game.source_url,
            source_id=self.id,
            game_id=game.game_id,
            fetcher=self._fetcher,
        )
        # The catalog's own title wins: a page title is often decorated with the
        # store name, while the catalog row is the provider's canonical label.
        found.title = game.title or found.title
        found.source_url = game.source_url
        return found


class StructuredHtmlSource(WebsiteSource):
    """Read public JSON-LD or Open Graph metadata from one HTML page.

    This is intentionally one page, not a crawler.  A page with an ItemList
    yields several entries; a VideoGame/Product page or Open Graph metadata
    yields one.  No links beyond the page's own structured data are followed.
    """

    name = "Structured HTML page"
    adapter_version = "1"
    response_format = "HTML with JSON-LD / Open Graph"
    pagination_method = "single page"
    source_kind = "structured"
    download_capability = "none"
    permitted_reason = (
        "The user explicitly asked to read structured metadata published on this "
        "public page; robots.txt and public-address checks apply."
    )

    def __init__(self, url: str, *, fetcher=None) -> None:
        super().__init__()
        split = urllib.parse.urlsplit(url)
        host = (split.hostname or "").lower()
        if split.scheme != "https" or not host:
            raise SourceError("Structured HTML discovery requires an HTTPS URL.")
        self.id = _source_key("html", url)
        self.name = f"Structured page — {host}"
        self.canonical_url = url
        self.hosts = (host,)
        self._fetcher = fetcher
        self._games_cache: list[SourceGame] | None = None

    def _get_html(self) -> str:
        try:
            return _fetch_html(
                self.canonical_url, name=self.name, fetcher=self._fetcher
            )
        except SourceError as exc:
            self.last_error = str(exc)
            raise

    @staticmethod
    def _jsonld_objects(html: str) -> list[Any]:
        objects: list[Any] = []
        pattern = re.compile(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(html):
            try:
                value = json.loads(html_module.unescape(match.group(1).strip()))
            except ValueError:
                continue
            objects.append(value)
        return objects

    @classmethod
    def _walk(cls, value: Any) -> list[Mapping[str, Any]]:
        if isinstance(value, list):
            found: list[Mapping[str, Any]] = []
            for item in value:
                found.extend(cls._walk(item))
            return found
        if not isinstance(value, Mapping):
            return []
        found = [value]
        graph = value.get("@graph")
        if isinstance(graph, list):
            found.extend(cls._walk(graph))
        return found

    @classmethod
    def _from_object(cls, obj: Mapping[str, Any]) -> list[dict[str, Any]]:
        kind = str(obj.get("@type") or "").casefold()
        if "itemlist" in kind:
            rows: list[dict[str, Any]] = []
            elements = obj.get("itemListElement")
            if isinstance(elements, list):
                for element in elements:
                    if not isinstance(element, Mapping):
                        continue
                    item = element.get("item") if isinstance(element.get("item"), Mapping) else element
                    if isinstance(item, Mapping):
                        rows.append(dict(item))
            return rows
        if any(token in kind for token in ("videogame", "product", "softwareapplication", "game")):
            return [dict(obj)]
        return []

    @staticmethod
    def _og(html: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for match in re.finditer(
            r'<meta\s+[^>]*property=["\'](og:[^"\']+)["\'][^>]*>',
            html,
            re.IGNORECASE,
        ):
            tag = match.group(0)
            content = re.search(
                r'content=["\']([^"\']*)["\']', tag, re.IGNORECASE
            )
            if content:
                values[match.group(1).casefold()] = html_module.unescape(
                    content.group(1)
                )
        return values

    def _load(self) -> list[SourceGame]:
        if self._games_cache is not None:
            return self._games_cache
        from .feeds import record_from_mapping

        html = self._get_html()
        rows = _structured_rows(html, self.canonical_url)
        games: list[SourceGame] = []
        for row in rows:
            game = record_from_mapping(
                row, source_id=self.id, fallback_url=self.canonical_url
            )
            if game is not None:
                games.append(game)
        if not games:
            raise SourceError(
                "No JSON-LD ItemList/VideoGame or Open Graph game metadata was "
                "found on this page."
            )
        self._games_cache = games
        return games

    def inspect(self) -> CapabilityReport:
        try:
            games = self._load()
        except SourceError as exc:
            return CapabilityReport(
                catalog=Capability.UNKNOWN,
                pagination=Capability.UNSUPPORTED,
                details=Capability.UNSUPPORTED,
                artwork=Capability.UNKNOWN,
                magnets=Capability.UNSUPPORTED,
                mirrors=Capability.UNSUPPORTED,
                reasons={"catalog": str(exc)},
            )
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.UNSUPPORTED,
            details=Capability.UNSUPPORTED,
            artwork=Capability.PROBABLE,
            magnets=Capability.UNSUPPORTED,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=len(games),
            reasons={
                "pagination": "Only the structured data on this page is read.",
                "details": "The page's structured data is the complete record.",
                "magnets": "HTML metadata never supplies magnets.",
                "mirrors": "HTML metadata never supplies download URLs.",
            },
            checked_at=_now(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        games = self._load()
        return SourcePage(
            games=list(games),
            next_cursor=None,
            total=len(games),
            page_number=1,
            url=self.canonical_url,
        )

    def details(self, game_id: str) -> SourceGame:
        raise UnsupportedOperation("The structured page is a single record source.")


#: One validating fetcher per host for ad-hoc page reads.  Sharing it means
#: robots.txt is fetched once per host (not once per record) and the policy's
#: per-host delay is actually honoured across the details pass.
_PAGE_FETCHERS: dict[str, Any] = {}
_PAGE_FETCHERS_LOCK = threading.Lock()


def _cached_fetcher(url: str, name: str):
    from .catalog import build_fetcher

    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    with _PAGE_FETCHERS_LOCK:
        fetcher = _PAGE_FETCHERS.get(host)
        if fetcher is None:
            fetcher = build_fetcher(_public_policy(url, name=name))
            _PAGE_FETCHERS[host] = fetcher
    return fetcher


def _fetch_html(url: str, *, name: str, fetcher=None) -> str:
    """Fetch one public HTML page through the shared safety policy."""
    from .catalog import CatalogError

    active = fetcher or _cached_fetcher(url, name)
    try:
        response = active.fetch(url)
    except CatalogError as exc:
        raise SourceError(f"The page could not be read: {exc}") from exc
    if response.status >= 400:
        raise SourceError(f"The page answered HTTP {response.status}.")
    if response.content_type and "html" not in response.content_type:
        raise SourceError(f"The page answered {response.content_type}, not HTML.")
    return response.body.decode("utf-8", "replace")


def _structured_rows(html: str, url: str) -> list[dict[str, Any]]:
    """JSON-LD ItemList/VideoGame/Product rows, else the Open Graph triple."""
    rows: list[dict[str, Any]] = []
    for value in StructuredHtmlSource._jsonld_objects(html):
        for obj in StructuredHtmlSource._walk(value):
            rows.extend(StructuredHtmlSource._from_object(obj))
    if rows:
        return rows
    og = StructuredHtmlSource._og(html)
    if og.get("og:title"):
        return [{
            "title": og.get("og:title", ""),
            "description": og.get("og:description", ""),
            "image": og.get("og:image", ""),
            "url": og.get("og:url") or url,
        }]
    return []


def page_metadata(
    url: str,
    *,
    source_id: str = "",
    game_id: str = "",
    fetcher=None,
) -> "SourceGame":
    """Read one game's own public page for details and artwork.

    Used by the enrichment pass of catalog sources whose records carry a
    canonical page URL.  Only the page's published JSON-LD/Open Graph is read:
    no links are followed, and robots.txt plus the public-address policy apply.
    """
    split = urllib.parse.urlsplit(url)
    host = (split.hostname or "").lower()
    if split.scheme != "https" or not host:
        raise UnsupportedOperation(
            "Only HTTPS source pages can supply details for this record."
        )
    from .feeds import record_from_mapping

    html = _fetch_html(url, name=f"details for {host}", fetcher=fetcher)
    rows = _structured_rows(html, url)
    if not rows:
        raise UnsupportedOperation(
            "This page publishes no JSON-LD or Open Graph game metadata."
        )
    record = record_from_mapping(
        rows[0],
        source_id=source_id or _source_key("page", url),
        fallback_url=url,
    )
    if record is None:
        raise UnsupportedOperation(
            "This page's structured metadata had no usable game title."
        )
    if game_id:
        record.game_id = game_id
    if source_id:
        record.source_id = source_id
    return record


def _now() -> float:
    import time

    return time.time()


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

SOURCE_TYPES: tuple[type[WebsiteSource], ...] = (
    ArchiveOrgSource,
    KryotoSource,
    FreeToGameSource,
    RawgSource,
)


def available_sources() -> tuple[type[WebsiteSource], ...]:
    return SOURCE_TYPES


def supported_hosts() -> tuple[str, ...]:
    hosts: list[str] = []
    for adapter in SOURCE_TYPES:
        hosts.extend(adapter.hosts)
    return tuple(sorted(set(hosts)))


def source_for_url(
    url: str,
    *,
    api_keys: Mapping[str, str] | None = None,
    allow_structured: bool = False,
    config: Any = None,
) -> WebsiteSource:
    """Build the adapter for a URL, or explain why no adapter exists."""
    text = (url or "").strip()
    if not text:
        raise SourceError("Enter a website URL.")
    try:
        split = urllib.parse.urlsplit(text)
    except ValueError as exc:
        raise SourceError(f"That is not a usable URL: {exc}") from exc
    if split.scheme.lower() not in ("http", "https"):
        raise SourceError("Only http:// and https:// URLs can be added.")
    if split.scheme.lower() == "http":
        text = "https://" + text.split("://", 1)[1]
        split = urllib.parse.urlsplit(text)
    if split.username or split.password:
        raise SourceError("URLs with a username or password are refused.")
    host = (split.hostname or "").lower()
    for adapter in SOURCE_TYPES:
        if adapter.matches(text):
            return adapter.from_url(text, api_keys=api_keys)
    if config not in (None, "", {}):
        # An explicit mapping wins over the generic JSON/sitemap guesses: the
        # user told us how this endpoint is shaped.
        return ConfiguredJsonSource(text, config)
    path = urllib.parse.urlsplit(text).path.lower()
    basename = path.rsplit("/", 1)[-1]
    if basename in SITEMAP_NAMES or (
        basename.startswith("sitemap") and basename.endswith(".xml")
    ):
        return SitemapSource(text)
    if path.endswith(JSON_CATALOG_SUFFIXES):
        return JsonCatalogSource(text)
    if allow_structured:
        return StructuredHtmlSource(text)
    names = ", ".join(sorted({adapter.name for adapter in SOURCE_TYPES}))
    raise SourceError(
        f"No adapter is registered for “{host or text}”. Supported sources: {names}, "
        "public sitemap XML URLs, and public .json/.jsonl/.ndjson catalog URLs. "
        "Entering an arbitrary page URL cannot discover an interface; use a "
        "documented API, a sitemap, a JSON catalog, or import a JSON/CSV file."
    )


__all__ = [
    "ArchiveOrgSource",
    "FREETOGAME_BASE",
    "FREETOGAME_SITE",
    "FreeToGameSource",
    "JsonCatalogSource",
    "KryotoSource",
    "RawgSource",
    "SitemapSource",
    "StructuredHtmlSource",
    "Capability",
    "CapabilityReport",
    "ConfiguredJsonSource",
    "DEFAULT_COLLECTION",
    "SourceError",
    "SourceGame",
    "SourceMirror",
    "SourcePage",
    "SOURCE_TYPES",
    "UnsupportedOperation",
    "WebsiteSource",
    "archive_collection_from_url",
    "available_sources",
    "normalize_source_config",
    "page_metadata",
    "request_json",
    "request_json_document",
    "source_for_url",
    "supported_hosts",
]
