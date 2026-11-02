"""Internet Archive client: collection listing, item metadata, file URLs.

Only public, unauthenticated endpoints are used:
  * advancedsearch.php  — collection membership
  * metadata/{id}        — item metadata and file list
  * download/{id}/{file} — file bytes

Responses are cached on disk to keep request volume low and to allow browsing
when the network is unavailable. No credentials are sent or stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .filetypes import classify_file, is_auxiliary, is_document
from .util import (
    ArchiveError,
    atomic_write_json,
    cache_dir,
    read_json,
    safe_filename,
    strip_html,
    validate_identifier,
)

COLLECTION = "native-linux-games-collection"
BASE_URL = "https://archive.org"
USER_AGENT = f"Lumora/{__version__} (personal desktop client)"
COLLECTION_TTL = 6 * 3600
ITEM_TTL = 24 * 3600


@dataclass(frozen=True)
class GameSummary:
    identifier: str
    title: str
    year: str
    downloads: int
    item_size: int | None
    creator: str = ""
    subjects: list[str] = field(default_factory=list)
    licenseurl: str = ""
    #: Set only for records imported from a website source.  The identifier is
    #: then a synthetic key (``catalog:<source>:<id>``), so archive.org item
    #: lookups are skipped for it and the catalog detail view is used instead.
    catalog_key: str = ""
    catalog_source: str = ""
    source_url: str = ""
    cover_url: str = ""
    #: For catalog records: ``ready``/``unverified``/``none``/``broken`` as
    #: reported by :meth:`lgl.catalog_store.CatalogStore.download_state`.
    download_state: str = ""

    @property
    def is_catalog(self) -> bool:
        return bool(self.catalog_key)

    @property
    def subtitle(self) -> str:
        bits: list[str] = []
        if self.year:
            bits.append(self.year)
        if self.item_size:
            from .util import human_size
            bits.append(human_size(self.item_size))
        return " · ".join(bits)


@dataclass(frozen=True)
class ArchiveFile:
    name: str
    size: int | None
    format: str
    md5: str = ""
    sha1: str = ""
    source: str = ""
    private: bool = False
    sha256: str = ""

    @property
    def kind(self) -> str:
        return classify_file(self.name, self.format)

    @property
    def auxiliary(self) -> bool:
        return is_auxiliary(self.name, self.format)

    @property
    def document(self) -> bool:
        return is_document(self.name)

    @property
    def downloadable(self) -> bool:
        return not self.private


@dataclass
class ArchiveItem:
    identifier: str
    title: str
    description: str
    creator: str
    date: str
    year: str
    licenseurl: str
    rights: str
    item_size: int | None
    subjects: list[str] = field(default_factory=list)
    files: list[ArchiveFile] = field(default_factory=list)
    restricted: bool = False

    @property
    def primary_files(self) -> list[ArchiveFile]:
        return [f for f in self.files if not f.auxiliary]

    @property
    def image_files(self) -> list[ArchiveFile]:
        """Raster images attached to the item (logos and screenshots)."""
        return [f for f in self.files if _is_raster_image(f.name)]

    @property
    def poster_file(self) -> ArchiveFile | None:
        """Best available cover/poster image, or None.

        Item logos are preferred, then screenshots, then the item thumbnail.
        This is only a UI hint; the image is fetched separately and cached.
        """
        candidates: list[tuple[int, int, ArchiveFile]] = []
        for f in self.image_files:
            if f.private:
                # A private file cannot be fetched, so it is not a usable poster.
                continue
            score = _poster_score(f)
            if score is None:
                continue
            candidates.append((score, -(f.size or 0), f))
        if not candidates:
            return None
        candidates.sort(key=lambda entry: (entry[0], entry[1]))
        return candidates[0][2]

    @property
    def poster_url(self) -> str:
        f = self.poster_file
        if f is not None:
            return self.file_url(f)
        # Public archive.org convenience endpoint. It returns an item image even
        # when no file is explicitly listed.
        return f"{BASE_URL}/services/img/{urllib.parse.quote(self.identifier)}"

    @property
    def screenshot_files(self) -> list[ArchiveFile]:
        shots = [
            f
            for f in self.image_files
            if _base_name(f.name).startswith("screenshot-")
            and "_thumb" not in _base_name(f.name).lower()
        ]
        shots.sort(key=lambda f: f.name.lower())
        return shots

    @property
    def download_url(self) -> str:
        return f"{BASE_URL}/details/{self.identifier}"

    def file_url(self, file: ArchiveFile | str) -> str:
        name = file.name if isinstance(file, ArchiveFile) else file
        return build_download_url(self.identifier, name)


_RASTER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _base_name(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _is_raster_image(name: str) -> bool:
    return _base_name(name).lower().endswith(_RASTER_SUFFIXES)


def _looks_like_image(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"\xff\xd8\xff"):  # JPEG
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):  # GIF
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WebP
        return True
    return False


def _poster_score(file: ArchiveFile) -> int | None:
    name = file.name.replace("\\", "/").lower()
    base = _base_name(name)
    if "_thumb" in base:
        return None
    if base.startswith("logo_") and not base.startswith("__ia_thumb"):
        return 0
    if "item_images" in name:
        return 1
    if base.startswith("screenshot-0"):
        return 2
    if base.startswith("screenshot-"):
        return 3
    if base == "__ia_thumb.jpg":
        return 4
    if (file.format or "").strip().lower() in ("jpeg", "png", "webp"):
        return 5
    return None


@dataclass(frozen=True)
class CollectionResult:
    games: list[GameSummary]
    fetched_at: float
    from_cache: bool
    warning: str | None = None


def build_download_url(identifier: str, filename: str) -> str:
    validate_identifier(identifier)
    quoted = urllib.parse.quote(filename, safe="/")
    return f"{BASE_URL}/download/{identifier}/{quoted}"


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _request_json(url: str, timeout: float = 30.0, retries: int = 2) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ArchiveError("archive.org returned an unreadable response") from exc
            if not isinstance(data, dict):
                raise ArchiveError("archive.org returned an unexpected response")
            return data
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                raise ArchiveError("The requested archive item was not found (404).") from exc
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt < retries:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = min(15.0, float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * (attempt + 1))
                    time.sleep(delay)
                    continue
                raise ArchiveError(
                    f"archive.org is rate-limiting or unavailable (HTTP {exc.code}). Try again shortly."
                ) from exc
            raise ArchiveError(f"archive.org request failed (HTTP {exc.code}).") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise ArchiveError("Could not reach archive.org. Check your network connection.") from exc
    raise ArchiveError(f"archive.org request failed: {last_error}")


class ArchiveClient:
    """Read-only client for the public collection."""

    def __init__(self, cache_root: Path | None = None, timeout: float = 30.0):
        self.cache_root = Path(cache_root) if cache_root else cache_dir()
        self.timeout = timeout
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._collection_cache = self.cache_root / "collection.json"
        self._item_cache = self.cache_root / "items"
        self._item_cache.mkdir(parents=True, exist_ok=True)

    # -- collection ---------------------------------------------------------

    def fetch_collection(self, force: bool = False) -> CollectionResult:
        cached = read_json(self._collection_cache, default=None)
        cached_ok = bool(
            cached
            and isinstance(cached.get("games"), list)
            and cached["games"]
        )
        if cached_ok and not force and time.time() - float(cached.get("fetched_at", 0)) < COLLECTION_TTL:
            return CollectionResult(
                games=_games_from_cache(cached),
                fetched_at=float(cached.get("fetched_at", 0)),
                from_cache=True,
            )

        params: list[tuple[str, str]] = [
            ("q", f"collection:{COLLECTION}"),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "year"),
            ("fl[]", "date"),
            ("fl[]", "downloads"),
            ("fl[]", "item_size"),
            ("fl[]", "creator"),
            ("fl[]", "subject"),
            ("fl[]", "licenseurl"),
            ("rows", "1000"),
            ("page", "1"),
            ("output", "json"),
            ("sort[]", "title asc"),
        ]
        url = f"{BASE_URL}/advancedsearch.php?" + urllib.parse.urlencode(params)
        try:
            data = _request_json(url, timeout=self.timeout)
            docs = data.get("response", {}).get("docs")
            if not isinstance(docs, list):
                raise ArchiveError("archive.org search response did not contain items")
            games = [_game_from_doc(doc) for doc in docs if isinstance(doc, dict)]
            games = [g for g in games if g is not None]
            games.sort(key=lambda g: g.title.casefold())
            fetched_at = time.time()
            atomic_write_json(
                self._collection_cache,
                {
                    "fetched_at": fetched_at,
                    "collection": COLLECTION,
                    "games": [g.__dict__ for g in games],
                },
            )
            return CollectionResult(games=games, fetched_at=fetched_at, from_cache=False)
        except ArchiveError as exc:
            if cached_ok:
                return CollectionResult(
                    games=_games_from_cache(cached),
                    fetched_at=float(cached.get("fetched_at", 0)),
                    from_cache=True,
                    warning=f"{exc} Showing the last saved list.",
                )
            raise

    # -- item ---------------------------------------------------------------

    def fetch_item(self, identifier: str, force: bool = False) -> ArchiveItem:
        validate_identifier(identifier)
        cache_path = self._item_cache / f"{identifier}.json"
        cached = read_json(cache_path, default=None)
        if (
            cached
            and isinstance(cached.get("item"), dict)
            and not force
            and time.time() - float(cached.get("fetched_at", 0)) < ITEM_TTL
        ):
            return _item_from_cache(cached["item"])

        url = f"{BASE_URL}/metadata/{urllib.parse.quote(identifier)}"
        data = _request_json(url, timeout=self.timeout)
        item = self._parse_item(data, identifier)
        atomic_write_json(
            cache_path,
            {"fetched_at": time.time(), "item": _item_to_dict(item)},
        )
        return item

    def fetch_image(self, url: str, cache_key: str,
                    max_bytes: int = 12 * 1024 * 1024,
                    timeout: float | None = None,
                    allowed_hosts: Sequence[str] | None = None) -> bytes:
        """Fetch and cache an approved image.

        With no ``allowed_hosts`` the archive.org rule is unchanged.  A website
        adapter passes its declared image hosts; those requests go through the
        public-address/robots/redirect guards in ``lgl.catalog.Fetcher``.
        """
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        hosts = tuple(allowed_hosts or ())
        archive_host = host == "archive.org" or host.endswith(".archive.org")
        if not hosts and not archive_host:
            raise ArchiveError("Refusing to fetch an image from outside archive.org.")
        if parsed.scheme != "https":
            raise ArchiveError("Only HTTPS image URLs are allowed.")
        if hosts and not any(
            host == allowed or host.endswith("." + allowed) for allowed in hosts
        ):
            raise ArchiveError(f"The image host {host!r} is not approved for this source.")
        key = safe_filename(cache_key or "image", "image")[:100]
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in _RASTER_SUFFIXES:
            suffix = ".img"
        image_dir = self.cache_root / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        cache_path = image_dir / f"{key}-{digest}{suffix}"

        if cache_path.exists():
            try:
                cached = cache_path.read_bytes()
                if cached and _looks_like_image(cached):
                    return cached
                cache_path.unlink(missing_ok=True)
            except OSError:
                pass

        limit = self.timeout if timeout is None else float(timeout)
        if hosts and not archive_host:
            # Reuse the catalog policy layer: DNS/private-address checks,
            # robots.txt, hop-by-hop redirect validation, and a byte cap.
            from .catalog import Fetcher, SourcePolicy

            policy = SourcePolicy(
                name="website artwork",
                hosts=frozenset(hosts),
                permitted_reason=(
                    "The configured source supplied this artwork URL as part of "
                    "its public metadata response."
                ),
                timeout=limit,
                max_bytes=max_bytes,
                min_delay=0.0,
            )
            try:
                response = Fetcher(policy).fetch(url)
            except Exception as exc:
                raise ArchiveError(f"Could not fetch the poster image: {exc}") from exc
            if response.status >= 400:
                raise ArchiveError(
                    f"Could not fetch the poster image (HTTP {response.status})."
                )
            data = response.body
            content_type = response.content_type
            if content_type and not content_type.startswith("image/"):
                raise ArchiveError(
                    f"The image URL returned {content_type}, not an image."
                )
        else:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "image/*",
                    "Accept-Encoding": "identity",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=limit) as response:
                    data = response.read(max_bytes + 1)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise ArchiveError("This item has no poster image.") from exc
                raise ArchiveError(
                    f"Could not fetch the poster image (HTTP {exc.code})."
                ) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
                raise ArchiveError("Could not reach archive.org to load the poster.") from exc
        if len(data) > max_bytes:
            raise ArchiveError("The poster image is unexpectedly large.")
        if not _looks_like_image(data):
            raise ArchiveError("The server returned something that is not an image.")

        fd, tmp_name = tempfile.mkstemp(prefix=cache_path.name + ".", suffix=".tmp", dir=str(image_dir))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, cache_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return data

    def _parse_item(self, data: dict[str, Any], identifier: str) -> ArchiveItem:
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        if data.get("is_dark"):
            raise ArchiveError("This archive item is unavailable (dark/removed).")
        restricted = str(metadata.get("access-restricted-item", "")).lower() == "true"

        files: list[ArchiveFile] = []
        for raw in data.get("files") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            item_file = ArchiveFile(
                name=name,
                size=_as_int(raw.get("size")),
                format=_as_text(raw.get("format")),
                md5=_as_text(raw.get("md5")),
                sha1=_as_text(raw.get("sha1")),
                source=_as_text(raw.get("source")),
                private=str(raw.get("private", "")).lower() == "true",
            )
            files.append(item_file)

        primary = [f for f in files if not f.auxiliary and f.size]
        total = _as_int(data.get("item_size"))
        if total is None:
            total = sum(f.size or 0 for f in primary) or None

        date = _as_text(metadata.get("date"))
        year = _as_text(metadata.get("year")) or (date[:4] if date else "")
        subjects = metadata.get("subject") or []
        if isinstance(subjects, str):
            subjects = [subjects]
        subjects = [str(s) for s in subjects] if isinstance(subjects, list) else []

        return ArchiveItem(
            identifier=identifier,
            title=_as_text(metadata.get("title")).strip() or identifier,
            description=strip_html(metadata.get("description")),
            creator=_as_text(metadata.get("creator")),
            date=date,
            year=year,
            licenseurl=_as_text(metadata.get("licenseurl")),
            rights=_as_text(metadata.get("rights")),
            item_size=total,
            subjects=subjects,
            files=files,
            restricted=restricted,
        )

    # -- search -------------------------------------------------------------

    @staticmethod
    def search(games: list[GameSummary], query: str) -> list[GameSummary]:
        terms = [t for t in query.casefold().split() if t]
        if not terms:
            return list(games)
        result = []
        for game in games:
            haystack = (
                f"{game.title} {game.identifier} {game.year} {game.creator} "
                f"{' '.join(game.subjects)} {game.licenseurl}"
            ).casefold()
            if all(term in haystack for term in terms):
                result.append(game)
        return result


def _game_from_doc(doc: dict[str, Any]) -> GameSummary | None:
    identifier = _as_text(doc.get("identifier")).strip()
    if not identifier:
        return None
    try:
        validate_identifier(identifier)
    except ValueError:
        return None
    year = _as_text(doc.get("year")) or _as_text(doc.get("date"))[:4]
    downloads = _as_int(doc.get("downloads")) or 0
    subjects = doc.get("subject") or []
    if isinstance(subjects, str):
        subjects = [subjects]
    return GameSummary(
        identifier=identifier,
        title=_as_text(doc.get("title")).strip() or identifier,
        year=year,
        downloads=downloads,
        item_size=_as_int(doc.get("item_size")),
        creator=_as_text(doc.get("creator")),
        subjects=[str(x) for x in subjects] if isinstance(subjects, list) else [],
        licenseurl=_as_text(doc.get("licenseurl")),
    )


def _games_from_cache(cached: dict[str, Any]) -> list[GameSummary]:
    games = []
    for raw in cached.get("games", []):
        if not isinstance(raw, dict):
            continue
        try:
            validate_identifier(str(raw.get("identifier", "")))
        except ValueError:
            continue
        games.append(
            GameSummary(
                identifier=str(raw["identifier"]),
                title=str(raw.get("title") or raw["identifier"]),
                year=str(raw.get("year") or ""),
                downloads=int(raw.get("downloads") or 0),
                item_size=_as_int(raw.get("item_size")),
                creator=str(raw.get("creator") or ""),
                subjects=[str(x) for x in raw.get("subjects", []) if isinstance(x, str)],
                licenseurl=str(raw.get("licenseurl") or ""),
            )
        )
    return games


def _item_to_dict(item: ArchiveItem) -> dict[str, Any]:
    return {
        "identifier": item.identifier,
        "title": item.title,
        "description": item.description,
        "creator": item.creator,
        "date": item.date,
        "year": item.year,
        "licenseurl": item.licenseurl,
        "rights": item.rights,
        "item_size": item.item_size,
        "subjects": item.subjects,
        "restricted": item.restricted,
        "files": [f.__dict__ for f in item.files],
    }


def _item_from_cache(raw: dict[str, Any]) -> ArchiveItem:
    files = []
    for f in raw.get("files", []):
        if not isinstance(f, dict) or not f.get("name"):
            continue
        files.append(
            ArchiveFile(
                name=str(f["name"]),
                size=_as_int(f.get("size")),
                format=str(f.get("format") or ""),
                md5=str(f.get("md5") or ""),
                sha1=str(f.get("sha1") or ""),
                source=str(f.get("source") or ""),
                private=bool(f.get("private")),
                sha256=str(f.get("sha256") or ""),
            )
        )
    return ArchiveItem(
        identifier=str(raw.get("identifier") or ""),
        title=str(raw.get("title") or raw.get("identifier") or ""),
        description=str(raw.get("description") or ""),
        creator=str(raw.get("creator") or ""),
        date=str(raw.get("date") or ""),
        year=str(raw.get("year") or ""),
        licenseurl=str(raw.get("licenseurl") or ""),
        rights=str(raw.get("rights") or ""),
        item_size=_as_int(raw.get("item_size")),
        subjects=[str(s) for s in raw.get("subjects", [])],
        files=files,
        restricted=bool(raw.get("restricted")),
    )
