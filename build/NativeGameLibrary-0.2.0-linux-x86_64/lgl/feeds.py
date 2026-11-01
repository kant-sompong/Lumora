"""Local JSON/CSV catalogs and tolerant field mapping.

These importers are deliberately separate from website adapters: a file has no
network interface, no pagination, and no credentials.  They still normalize
every record into :class:`~lgl.sources.SourceGame`, so the same catalog store,
deduplication, Browse merge, cancellation, and resume machinery applies.

Mapping is alias based.  Values are only used when the file actually contains
them; nothing is inferred from a title, and a record without a title is skipped.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import metadata as meta
from .sources import (
    Capability,
    CapabilityReport,
    SourceError,
    SourceGame,
    SourcePage,
    UnsupportedOperation,
    WebsiteSource,
)

MAX_FILE_BYTES = 64 * 1024 * 1024
LOCAL_PAGE_SIZE = 500
FILE_SOURCE_PREFIX = "file:"
#: Artwork hosts remembered for a file source, so imported covers may load.
MAX_LOCAL_IMAGE_HOSTS = 12


def _image_hosts(games: Sequence[SourceGame]) -> tuple[str, ...]:
    """HTTPS hosts this catalog's own records point at for pages and artwork."""
    hosts: list[str] = []
    for game in games:
        for url in (game.cover_url, game.source_url):
            if not url:
                continue
            split = urllib.parse.urlsplit(url)
            host = (split.hostname or "").lower()
            if split.scheme != "https" or not host or host in hosts:
                continue
            hosts.append(host)
            if len(hosts) >= MAX_LOCAL_IMAGE_HOSTS:
                return tuple(hosts)
    return tuple(hosts)


def _sidecar_urls(game: SourceGame) -> list[str]:
    """``.json`` links a record publishes for itself (its magnet sidecar)."""
    from .sidecar import json_urls

    return json_urls(_record_strings(game.extra))


def _record_strings(value: Any, depth: int = 0) -> list[str]:
    """Every string inside a record's preserved provider fields."""
    found: list[str] = []
    if depth > 4:
        return found
    if isinstance(value, Mapping):
        for item in value.values():
            found.extend(_record_strings(item, depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_record_strings(item, depth + 1))
    elif isinstance(value, str):
        found.append(value)
    return found


def _sidecar_hosts(games: Sequence[SourceGame]) -> tuple[str, ...]:
    hosts: list[str] = []
    for game in games:
        for url in _sidecar_urls(game):
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
            if host and host not in hosts:
                hosts.append(host)
                if len(hosts) >= MAX_LOCAL_IMAGE_HOSTS:
                    return tuple(hosts)
    return tuple(hosts)


def _has_page_url(game: SourceGame) -> bool:
    split = urllib.parse.urlsplit(game.source_url or "")
    return split.scheme == "https" and bool(split.hostname)


class FeedError(SourceError):
    """A local catalog file could not be read or understood."""


#: Lower-case alias -> canonical field.  Dot paths (``platform.name``) are
#: supported by ``_pick`` so nested JSON can be mapped without a custom adapter.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": (
        "id", "game_id", "slug", "uuid", "guid", "appid", "app_id",
        "product_id", "productid", "sku",
    ),
    "title": ("title", "name", "game", "game_name"),
    "description": (
        "description", "description_raw", "summary", "about",
        "short_description",
    ),
    # schema.org JSON-LD uses camelCase keys (releaseDate, datePublished) which
    # the casefolded lookup matches through these lowercase aliases.
    "release_date": (
        "release_date", "released", "release", "releasedate", "datepublished",
        "date", "release_timestamp", "released_at", "published_at",
        "publish_date", "publication_date", "timestamp",
    ),
    "year": ("year", "release_year"),
    "developer": (
        "developer", "developers", "developer_name", "author", "creator",
    ),
    "publisher": ("publisher", "publishers", "publisher_name"),
    "genres": ("genres", "genre", "categories", "category"),
    "platforms": (
        "platforms", "platform", "systems", "os", "operatingsystem",
        # schema.org VideoGame spells this one in camelCase.
        "gameplatform", "gameplatforms",
    ),
    "cover_url": (
        "cover_url", "cover", "image", "image_url", "background_image",
        "thumbnail", "thumbnailurl", "header_image", "poster", "poster_url",
        "artwork", "box_art", "boxart", "coverimage", "cover_image",
    ),
    "source_url": (
        "source_url", "url", "link", "page_url", "website", "pageurl",
        "canonical_url", "canonicalurl", "detail_url", "product_url",
        "permalink", "href", "store_url",
    ),
    "rating": ("rating", "score", "metacritic"),
    "tags": ("tags", "keywords"),
    "stores": ("stores", "store_links"),
}

#: Unknown record fields are kept here as informational data, never as links.
PRESERVED_FIELDS_KEY = "source_fields"
MAX_PRESERVED_FIELDS = 24
MAX_PRESERVED_VALUE = 200
#: Every spelling a catalog may use for its own record identifier.
_ID_ALIASES = frozenset(FIELD_ALIASES["id"])


def _identity_fields(raw: Mapping[str, Any], game_id: str) -> dict[str, Any]:
    """Keep a record's own identifier values beside the id actually chosen.

    ``id`` consumes the first matching alias (``slug`` before ``productId``),
    so the key that was not used would otherwise be dropped as "known".  Those
    values are catalog keys, not links: they are kept verbatim as text so a
    later import can still match the record on its real primary key.
    """
    kept: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name or len(name) > 48:
            continue
        if name.casefold() not in _ID_ALIASES and _snake(name) not in _ID_ALIASES:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        text = meta.clean_text(value, MAX_PRESERVED_VALUE)
        if not text or text == game_id:
            continue
        kept[name] = text
    return kept


def _preserved_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a record's unrecognised scalar fields without interpreting them.

    This is a lossless side channel: ``hasTorrent`` stays a flag, ``md5Url``
    stays an opaque string.  Nothing here becomes a mirror, a magnet, or a
    download, and nothing is fetched.
    """
    kept: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name or len(name) > 48 or name.casefold() in _KNOWN_KEYS:
            continue
        if isinstance(value, bool):
            kept[name] = value
        elif isinstance(value, (int, float)):
            kept[name] = value
        elif isinstance(value, str):
            text = meta.clean_text(value, MAX_PRESERVED_VALUE)
            if text:
                kept[name] = text
        else:
            # Nested objects and lists are not flattened into single values.
            continue
        if len(kept) >= MAX_PRESERVED_FIELDS:
            break
    return kept


_EXTRA_SCALARS = (
    "rating", "metacritic", "score", "playtime", "ratings_count", "added",
    "updated", "suggestions_count", "reviews_count",
)

#: Keys already handled by a canonical field; not repeated as provider fields.
_KNOWN_KEYS = frozenset(
    alias for aliases in FIELD_ALIASES.values() for alias in aliases if "." not in alias
).union({"tags", "stores", "genres", "platforms"}, set(_EXTRA_SCALARS))


def _flatten_keys(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Casefolded keys, plus a snake_case form for camelCase catalog fields.

    ``pageUrl`` and ``page_url`` must map to the same alias, so both spellings
    are registered.  The original spelling wins when they collide.
    """
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        original = str(key).strip()
        name = original.casefold()
        if name and name not in flat:
            flat[name] = value
        snake = _snake(original)
        if snake and snake != name and snake not in flat:
            flat[snake] = value
    return flat


def _snake(name: str) -> str:
    """``datePublished`` -> ``date_published``; acronyms are left alone."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).casefold()


def _pick(
    raw: Mapping[str, Any],
    field: str,
    flat: Mapping[str, Any] | None = None,
    field_map: Mapping[str, str] | None = None,
) -> Any:
    """Read one canonical field, honouring a user mapping before the aliases."""
    if flat is None:
        flat = _flatten_keys(raw)
    if field_map and field in field_map:
        mapped = str(field_map[field]).strip()
        for name in {mapped.casefold(), _snake(mapped)}:
            if name in flat and flat[name] not in (None, ""):
                return flat[name]
    for alias in FIELD_ALIASES[field]:
        if alias in flat:
            value = flat[alias]
            if value not in (None, ""):
                return value
        if "." in alias:
            current: Any = raw
            for part in alias.split("."):
                if not isinstance(current, Mapping):
                    current = None
                    break
                current = current.get(part)
            if current not in (None, ""):
                return current
    return None


def _labels(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        name = value.get("name") or value.get("title") or value.get("label")
        return meta.clean_list([name] if isinstance(name, str) else [])
    if isinstance(value, (list, tuple)):
        names: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                name = item.get("name") or item.get("title") or item.get("label")
                if isinstance(name, str):
                    names.append(name)
            elif isinstance(item, str):
                names.append(item)
        return meta.clean_list(names)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                return _labels(json.loads(text))
            except ValueError:
                pass
        return meta.clean_list(text)
    return ()


def _platform_labels(value: Any) -> tuple[str, ...]:
    """Turn ``{"windows": true, "linux": false}`` into ``("Windows",)``."""
    if isinstance(value, Mapping) and value and all(
        isinstance(flag, bool) for flag in value.values()
    ):
        names = [
            _PLATFORM_NAMES.get(str(name).casefold(), str(name).strip().capitalize())
            for name, enabled in value.items()
            if enabled
        ]
        return meta.clean_list(names)
    return _labels(value)


_PLATFORM_NAMES = {
    "windows": "Windows",
    "win": "Windows",
    "macos": "macOS",
    "mac": "macOS",
    "osx": "macOS",
    "linux": "Linux",
    "android": "Android",
    "ios": "iOS",
    "web": "Web",
    "browser": "Web",
}


def _release_value(value: Any) -> str:
    """Accept an ISO-ish date or a Unix timestamp in seconds/milliseconds."""
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return ""
        if text.isdigit() and len(text) in (10, 13):
            number = float(text)
        else:
            return meta.clean_release_date(text)
    if number > 1e11:  # milliseconds
        number /= 1000.0
    if not 0 < number < 4_102_444_800:  # before 2100
        return ""
    import time as _time

    try:
        return _time.strftime("%Y-%m-%d", _time.gmtime(number))
    except (OSError, OverflowError, ValueError):
        return ""


def _stable_id(raw: Mapping[str, Any], title: str, source_url: str,
               flat: Mapping[str, Any] | None = None,
               field_map: Mapping[str, str] | None = None) -> str:
    value = _pick(raw, "id", flat, field_map)
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-.")
        if text:
            return text[:140]
    basis = source_url or f"{title}|{_pick(raw, 'year') or _pick(raw, 'release_date') or ''}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"rec-{digest}"


def record_from_mapping(
    raw: Mapping[str, Any],
    *,
    source_id: str,
    fallback_url: str = "",
    field_map: Mapping[str, str] | None = None,
) -> SourceGame | None:
    """Normalize one JSON object or CSV row, or return None if it has no title.

    ``field_map`` maps a canonical field name to the source's own key, so a
    catalog with unusual names can be read without new code.
    """
    if not isinstance(raw, Mapping):
        return None
    flat = _flatten_keys(raw)
    pick = lambda field: _pick(raw, field, flat, field_map)  # noqa: E731
    title = meta.clean_text(pick("title"), 300)
    if not title:
        return None
    source_url = meta.clean_url(pick("source_url")) or meta.clean_url(fallback_url)
    release_date = _release_value(pick("release_date")) or _release_value(
        pick("year")
    )
    year = meta.clean_year(pick("year")) or meta.clean_year(release_date) or (
        release_date[:4] if release_date else ""
    )
    extra: dict[str, Any] = {}
    for name in _EXTRA_SCALARS:
        value = raw.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            extra[name] = value
        elif isinstance(value, str) and value.strip():
            extra[name] = meta.clean_text(value, 120)
    game_id = _stable_id(raw, title, source_url, flat, field_map)
    # Identity fields the id aliases consumed stay available, so productId is
    # not lost just because slug was the first alias to match.
    preserved = _identity_fields(raw, game_id)
    preserved.update(
        {
            name: value
            for name, value in _preserved_fields(raw).items()
            if name not in preserved
        }
    )
    if preserved:
        extra[PRESERVED_FIELDS_KEY] = dict(
            list(preserved.items())[:MAX_PRESERVED_FIELDS]
        )
    tags = _labels(pick("tags"))
    if tags:
        extra["tags"] = list(tags)
    stores = _pick(raw, "stores", flat, field_map)
    if isinstance(stores, (list, tuple)):
        links: list[dict[str, str]] = []
        for item in stores:
            if isinstance(item, Mapping):
                name = meta.clean_text(item.get("name") or item.get("store"), 80)
                url = meta.clean_url(item.get("url") or item.get("link"))
                if name or url:
                    links.append({"name": name, "url": url})
        if links:
            extra["stores"] = links
    return SourceGame(
        source_id=source_id,
        game_id=game_id,
        title=title,
        year=year,
        creator=", ".join(_labels(pick("developer"))),
        developer=", ".join(_labels(pick("developer"))),
        publisher=", ".join(_labels(pick("publisher"))),
        release_date=release_date,
        genres=_labels(pick("genres")),
        platforms=_platform_labels(pick("platforms")),
        description=meta.clean_description(pick("description")),
        cover_url=meta.clean_url(pick("cover_url")),
        source_url=source_url,
        extra=extra,
    )


def _items_from_json(data: Any) -> list[Mapping[str, Any]]:
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, Mapping):
        candidates = None
        for key in ("games", "results", "items", "data", "catalog", "records"):
            value = data.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, Mapping):
                for inner in ("games", "results", "items"):
                    if isinstance(value.get(inner), list):
                        candidates = value[inner]
                        break
            if candidates is not None:
                break
            if key in data and key in (
                "games", "results", "items", "data", "catalog", "records"
            ):
                raise FeedError(
                    f"The JSON field {key!r} is present but is not a list of records."
                )
        if candidates is None:
            # A single game object is still a valid one-record catalog.
            candidates = [data]
    else:
        raise FeedError("The JSON document is not an object or a list.")
    return [item for item in candidates if isinstance(item, Mapping)]


def items_from_json(data: Any) -> list[Mapping[str, Any]]:
    """Public helper used by the remote JSON catalog adapter."""
    return _items_from_json(data)


def parse_json_catalog(text: str) -> list[Mapping[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise FeedError(f"The file is not valid JSON: {exc}") from exc
    return _items_from_json(data)


def parse_csv_catalog(text: str) -> list[Mapping[str, Any]]:
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise FeedError("The CSV file has no header row.")
        return [dict(row) for row in reader]
    except csv.Error as exc:
        raise FeedError(f"The file is not valid CSV: {exc}") from exc


def read_catalog_file(path: Path | str) -> list[Mapping[str, Any]]:
    file_path = Path(path).expanduser()
    try:
        if file_path.stat().st_size > MAX_FILE_BYTES:
            raise FeedError(
                f"The catalog file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB."
            )
        text = file_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise FeedError(f"The catalog file could not be read: {exc}") from exc
    suffix = file_path.suffix.casefold()
    if suffix == ".csv":
        rows = parse_csv_catalog(text)
    elif suffix in (".json", ".jsonl", ".ndjson"):
        if suffix in (".jsonl", ".ndjson"):
            rows = []
            for number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except ValueError as exc:
                    raise FeedError(f"Line {number} is not valid JSON: {exc}") from exc
                if isinstance(item, Mapping):
                    rows.append(item)
        else:
            rows = parse_json_catalog(text)
    else:
        raise FeedError("Choose a .json, .jsonl, .ndjson, or .csv catalog file.")
    if not rows:
        raise FeedError("The file contains no game records.")
    return rows


class LocalFileSource(WebsiteSource):
    """A one-file JSON/CSV catalog, paged in memory for large imports."""

    id = FILE_SOURCE_PREFIX + "catalog"
    name = "Local catalog file"
    canonical_url = ""
    hosts = ()
    adapter_version = "1"
    response_format = "JSON/CSV file"
    pagination_method = "record offset"
    source_kind = "file"
    download_capability = "store-links"
    permitted_reason = "The user explicitly chose this local file."

    def __init__(self, path: Path | str, *, read_sidecars: bool = False) -> None:
        super().__init__()
        self.path = Path(path).expanduser().resolve()
        digest = hashlib.sha1(str(self.path).encode("utf-8")).hexdigest()[:12]
        self.id = f"{FILE_SOURCE_PREFIX}{digest}"
        self.name = f"{self.path.stem or 'catalog'} (file)"
        self.canonical_url = self.path.as_uri()
        #: Set only when the user explicitly asked to follow the ``.json`` links
        #: the file itself publishes.  Without this, a local catalog is metadata
        #: only and the sidecar reader refuses it.
        self.read_sidecars = bool(read_sidecars)
        self._rows = read_catalog_file(self.path)
        self._games = [
            game
            for raw in self._rows
            if (game := record_from_mapping(
                raw, source_id=self.id, fallback_url=self.path.as_uri()
            )) is not None
        ]
        if not self._games:
            raise FeedError("No record in the file had a usable title.")
        self._by_id = {game.game_id: game for game in self._games}
        #: Artwork hosts the file itself named; the window approves only these.
        self.image_hosts = _image_hosts(self._games)
        #: Records whose own page can be read for details during enrichment.
        self.detail_records = sum(1 for game in self._games if _has_page_url(game))
        if self.read_sidecars:
            # The user chose this file and asked for its own ``.json`` links to
            # be read, so the per-record sidecar may publish a magnet.  Only the
            # hosts those links name are approved for the request.
            self.download_capability = "authorized-files"
            self.hosts = _sidecar_hosts(self._games)

    def inspect(self) -> CapabilityReport:
        detailed = self.detail_records
        sidecars = sum(1 for game in self._games if _sidecar_urls(game))
        magnets = (
            Capability.PROBABLE
            if self.read_sidecars and sidecars
            else Capability.UNSUPPORTED
        )
        return CapabilityReport(
            catalog=Capability.VERIFIED,
            pagination=Capability.VERIFIED,
            details=Capability.PROBABLE if detailed else Capability.UNSUPPORTED,
            artwork=Capability.PROBABLE,
            magnets=magnets,
            mirrors=Capability.UNSUPPORTED,
            total_estimate=len(self._games),
            reasons={
                "details": (
                    f"{detailed} record(s) carry their own HTTPS page; the details "
                    "pass reads that page's published JSON-LD/Open Graph metadata."
                    if detailed else
                    "No record in the file carries a source page URL, so there is "
                    "nothing to open for details."
                ),
                "artwork": (
                    f"Artwork hosts named by the file: {', '.join(self.image_hosts)}."
                    if self.image_hosts else
                    "No record in the file carries a cover URL."
                ),
                "magnets": (
                    f"{sidecars} record(s) publish their own .json link. Reading "
                    "them is enabled, so each link is fetched once, and a magnet or "
                    "infohash it contains is kept as unverified metadata."
                    if magnets is Capability.PROBABLE else
                    (
                        f"{sidecars} record(s) publish their own .json link, but "
                        "reading them is off for this file."
                        if sidecars else
                        "No record in the file carries a .json link to read."
                    )
                ),
                "mirrors": "A local catalog never invents download URLs.",
            },
            checked_at=__import__("time").time(),
        )

    def page(self, cursor: str | None = None, limit: int = 100) -> SourcePage:
        try:
            offset = int(cursor) if cursor not in (None, "") else 0
        except (TypeError, ValueError):
            raise FeedError(f"Unusable file cursor: {cursor!r}") from None
        if offset < 0:
            raise FeedError(f"Unusable file cursor: {cursor!r}")
        limit = max(1, min(int(limit), LOCAL_PAGE_SIZE))
        chunk = self._games[offset:offset + limit]
        next_offset = offset + len(chunk)
        complete = next_offset >= len(self._games)
        return SourcePage(
            games=list(chunk),
            next_cursor=None if complete else str(next_offset),
            total=len(self._games),
            page_number=offset // limit + 1,
            # The importer watches page URLs for a source that stops advancing,
            # so each page needs its own identity even though one file backs it.
            url=f"{self.path.as_uri()}#offset={offset}",
        )

    def details(self, game_id: str) -> SourceGame:
        """Open the record's own page and read its published metadata.

        A record with no HTTPS page, or a page without structured data, is an
        unsupported detail source rather than a failure: the importer keeps the
        catalog row and moves on.
        """
        from .sources import page_metadata

        game = self._by_id.get(game_id)
        if game is None:
            raise SourceError(f"{game_id} is not in this catalog file.")
        if not _has_page_url(game):
            raise UnsupportedOperation(
                "This record has no HTTPS source page to read details from."
            )
        found = page_metadata(
            game.source_url, source_id=self.id, game_id=game.game_id
        )
        # Never let a page replace the identity or canonical URL we already have.
        found.title = found.title or game.title
        found.source_url = game.source_url
        return found

    def mirrors(self, game_id: str):
        raise UnsupportedOperation("A local catalog supplies no download files.")

    def magnets(self, game_id: str):
        raise UnsupportedOperation("A local catalog supplies no magnets.")


def local_source(path: Path | str, *, read_sidecars: bool = False) -> LocalFileSource:
    return LocalFileSource(path, read_sidecars=read_sidecars)


__all__ = [
    "FeedError",
    "LocalFileSource",
    "MAX_FILE_BYTES",
    "items_from_json",
    "parse_csv_catalog",
    "parse_json_catalog",
    "read_catalog_file",
    "record_from_mapping",
    "local_source",
]
