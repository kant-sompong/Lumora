"""Durable catalog state: sources, games, magnets, mirrors, and import jobs.

The application's own library index (``library_store.py``) tracks folders that
exist on disk.  An imported *catalog* is different: it is remote metadata, it can
be thousands of records, and it must never be mistaken for an installed game.
This module keeps that data in its own JSON document with explicit tables:

``sources``   configured adapters (URL, capabilities snapshot, last sync)
``games``     one record per (source, source game id)
``magnets``   validated magnet URIs, de-duplicated by infohash
``mirrors``   download URLs reported by a source, de-duplicated by URL
``jobs``      import state, checkpoints, counters, and an error log

Writes are atomic and whole-document for now: a few megabytes for a full
catalog, written once per imported page.  That is deliberately simple and keeps
a crashed import from leaving a half-written index behind.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import magnet as magnetlib
from . import metadata as meta
from .sources import SourceGame, SourceMirror
from .util import atomic_write_json, parse_size, read_json

SCHEMA_VERSION = 1
MAX_ERRORS_PER_JOB = 200

JOB_STATUSES = (
    "not started",
    "discovering",
    "importing",
    "partial",
    "complete",
    "interrupted",
    "blocked",
    "failed",
)

#: What is actually known about one game's download links.  ``ready`` needs a
#: check that really ran; a magnet is valid text, not proof the swarm has the
#: content, so it stays ``unverified``.
DOWNLOAD_READY = "ready"
DOWNLOAD_UNVERIFIED = "unverified"
DOWNLOAD_NONE = "none"
DOWNLOAD_BROKEN = "broken"
DOWNLOAD_STATES = (
    DOWNLOAD_READY, DOWNLOAD_UNVERIFIED, DOWNLOAD_NONE, DOWNLOAD_BROKEN,
)

DOWNLOAD_STATE_LABELS = {
    DOWNLOAD_READY: "link verified",
    DOWNLOAD_UNVERIFIED: "link unchecked",
    DOWNLOAD_NONE: "no download link",
    DOWNLOAD_BROKEN: "link broken",
}

#: Ascending order for the Download sort: usable first, broken last.
DOWNLOAD_STATE_RANK = {
    DOWNLOAD_READY: 0,
    DOWNLOAD_UNVERIFIED: 1,
    DOWNLOAD_NONE: 2,
    DOWNLOAD_BROKEN: 3,
}

#: Provider fields that may carry a size the source itself reported.
_SIZE_FIELDS = (
    "size", "download_size", "file_size", "filesize", "install_size", "bytes",
)


def _now() -> float:
    return time.time()


def _title_key(title: str) -> str:
    return " ".join(str(title or "").casefold().split())


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _coherent_status(status: Any, kind: Any) -> str:
    """The stored check status, with an unclassifiable "verified" dropped.

    ``verified`` is the probe's word for "a file response was seen", and a
    probe always records the kind beside it.  A record that carries the word
    without the classification (an older catalogue, or one imported with a
    status and no kind) cannot be trusted as a download, so it loads as
    ``unchecked`` and is probed again on request.
    """
    text = str(status or "unchecked")
    if text not in MirrorRecord.STATUSES:
        return "unchecked"
    if text == "verified" and str(kind or "") != "file":
        return "unchecked"
    return text


@dataclass
class CatalogGame:
    """One imported game record, keyed by its source and source game id."""

    source_id: str = ""
    game_id: str = ""
    title: str = ""
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
    first_seen: float = 0.0
    updated_at: float = 0.0
    #: Set when another record with the same title and year already exists.
    duplicate_of: str = ""
    #: ``{field: {"kept": ..., "incoming": ...}}`` for values that disagreed.
    conflicts: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Source-specific values worth keeping without polluting the core fields.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.game_id}"

    @property
    def normalized_title(self) -> str:
        return _title_key(self.title)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "game_id": self.game_id,
            "title": self.title,
            "year": self.year,
            "creator": self.creator,
            "developer": self.developer,
            "publisher": self.publisher,
            "release_date": self.release_date,
            "genres": list(self.genres),
            "platforms": list(self.platforms),
            "description": self.description,
            "cover_url": self.cover_url,
            "source_url": self.source_url,
            "first_seen": self.first_seen,
            "updated_at": self.updated_at,
            "duplicate_of": self.duplicate_of,
            "conflicts": {k: dict(v) for k, v in self.conflicts.items()},
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "CatalogGame | None":
        if not isinstance(raw, Mapping):
            return None
        source_id = raw.get("source_id")
        game_id = raw.get("game_id")
        if not isinstance(source_id, str) or not source_id.strip():
            return None
        if not isinstance(game_id, str) or not game_id.strip():
            return None
        conflicts: dict[str, dict[str, str]] = {}
        raw_conflicts = raw.get("conflicts")
        if isinstance(raw_conflicts, Mapping):
            for name, value in raw_conflicts.items():
                if isinstance(value, Mapping):
                    conflicts[str(name)] = {
                        str(k): str(v) for k, v in value.items()
                    }
        extra = raw.get("extra")
        return cls(
            source_id=source_id.strip(),
            game_id=game_id.strip(),
            title=meta.clean_text(raw.get("title"), 300),
            year=meta.clean_year(raw.get("year")),
            creator=meta.clean_text(raw.get("creator")),
            developer=meta.clean_text(raw.get("developer")),
            publisher=meta.clean_text(raw.get("publisher")),
            release_date=meta.clean_release_date(raw.get("release_date")),
            genres=meta.clean_list(raw.get("genres")),
            platforms=meta.clean_list(raw.get("platforms")),
            description=meta.clean_description(raw.get("description")),
            cover_url=meta.clean_url(raw.get("cover_url")),
            source_url=meta.clean_url(raw.get("source_url")),
            first_seen=_as_float(raw.get("first_seen")),
            updated_at=_as_float(raw.get("updated_at")),
            duplicate_of=str(raw.get("duplicate_of") or ""),
            conflicts=conflicts,
            extra=dict(extra) if isinstance(extra, Mapping) else {},
        )


@dataclass
class MagnetRecord:
    """A validated magnet URI and every source reference that reported it."""

    infohash: str = ""
    uri: str = ""
    display_name: str = ""
    origin: str = "extracted"
    method: str = ""
    discovered_at: float = 0.0
    validated_at: float = 0.0
    user_verified: bool = False
    refs: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_link(
        cls,
        link: magnetlib.MagnetLink,
        *,
        source_id: str,
        game_key: str,
        source_url: str = "",
        method: str = "",
    ) -> "MagnetRecord":
        stamp = _now()
        return cls(
            infohash=link.infohash,
            uri=link.uri,
            display_name=link.display_name,
            origin=link.origin,
            method=method,
            discovered_at=stamp,
            validated_at=stamp,
            refs=[
                {
                    "source_id": source_id,
                    "game_key": game_key,
                    "source_url": source_url,
                }
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "infohash": self.infohash,
            "uri": self.uri,
            "display_name": self.display_name,
            "origin": self.origin,
            "method": self.method,
            "discovered_at": self.discovered_at,
            "validated_at": self.validated_at,
            "user_verified": self.user_verified,
            "refs": [dict(ref) for ref in self.refs],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "MagnetRecord | None":
        if not isinstance(raw, Mapping):
            return None
        infohash = magnetlib.normalize_infohash(raw.get("infohash"))
        if not infohash:
            return None
        uri = raw.get("uri")
        try:
            link = magnetlib.parse_magnet(uri) if isinstance(uri, str) else None
        except magnetlib.MagnetError:
            link = None
        if link is None:
            # A stored magnet without a usable URI is not evidence of anything;
            # rebuilding one from the hash alone would be inventing a record.
            return None
        if link.infohash != infohash:
            return None
        refs = raw.get("refs")
        return cls(
            infohash=infohash,
            uri=link.uri if link is not None else "",
            display_name=meta.clean_text(raw.get("display_name")),
            origin=str(raw.get("origin") or (link.origin if link else "extracted")),
            method=meta.clean_text(raw.get("method"), 60),
            discovered_at=_as_float(raw.get("discovered_at")),
            validated_at=_as_float(raw.get("validated_at")),
            user_verified=bool(raw.get("user_verified", False)),
            refs=[
                {
                    "source_id": str(ref.get("source_id") or ""),
                    "game_key": str(ref.get("game_key") or ""),
                    "source_url": meta.clean_url(ref.get("source_url")),
                }
                for ref in refs
                if isinstance(ref, Mapping)
            ] if isinstance(refs, list) else [],
        )


@dataclass
class MirrorRecord:
    """A download URL reported by a source, with the source's own claims."""

    source_id: str = ""
    game_key: str = ""
    url: str = ""
    filename: str = ""
    size: int = 0
    format: str = ""
    checksum_algorithm: str = ""
    checksum_value: str = ""
    version: str = ""
    discovered_at: float = 0.0
    last_checked: float = 0.0
    #: ``unchecked`` until a check actually runs.  Never claim more than that.
    check_status: str = "unchecked"
    requires_auth: bool = False
    #: The provider's own label for the link (falling back to its filename).
    label: str = ""
    #: The provider's declared host, or the URL's host.
    host: str = ""
    #: A shape (``file-candidate``, ``webpage``, ``provider-endpoint``), never
    #: proof that the destination is a file.
    url_kind: str = ""
    #: What a check actually observed, when one ran.
    content_type: str = ""
    notes: tuple[str, ...] = ()
    #: The provider's original URL if it was relative.
    raw_url: str = ""

    #: The only statuses a mirror record may carry.
    STATUSES = (
        "unchecked", "verified", "page", "missing", "failed",
        "blocked", "external", "unsupported",
    )

    def status_label(self) -> str:
        """A short, honest description of what is known about this link."""
        return {
            "unchecked": "present · not verified",
            "verified": "verified by a request",
            "page": "reachable page · not a file",
            "missing": "not found",
            "failed": "check failed",
            "blocked": "access blocked or not permitted",
            "external": "external host · not checked",
            "unsupported": "not supported by this source",
        }.get(self.check_status, "present · not verified")

    @classmethod
    def from_source(
        cls, mirror: SourceMirror, *, source_id: str, game_key: str
    ) -> "MirrorRecord":
        return cls(
            source_id=source_id,
            game_key=game_key,
            url=meta.clean_url(mirror.url),
            filename=meta.clean_text(mirror.filename, 260),
            size=max(0, int(mirror.size or 0)),
            format=meta.clean_text(mirror.format, 40),
            checksum_algorithm=meta.clean_text(mirror.checksum_algorithm, 20).lower(),
            checksum_value=meta.clean_text(mirror.checksum_value, 200),
            version=meta.clean_text(mirror.version, 60),
            label=meta.clean_text(mirror.label, 260),
            host=meta.clean_text(mirror.host, 120),
            url_kind=meta.clean_text(mirror.url_kind, 40),
            raw_url=str(mirror.raw_url or "")[:2048],
            requires_auth=bool(mirror.requires_auth),
            notes=tuple(meta.clean_text(note, 400) for note in mirror.notes)[:8],
            discovered_at=_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "game_key": self.game_key,
            "url": self.url,
            "filename": self.filename,
            "size": self.size,
            "format": self.format,
            "checksum_algorithm": self.checksum_algorithm,
            "checksum_value": self.checksum_value,
            "version": self.version,
            "discovered_at": self.discovered_at,
            "last_checked": self.last_checked,
            "check_status": self.check_status,
            "requires_auth": self.requires_auth,
            "label": self.label,
            "host": self.host,
            "url_kind": self.url_kind,
            "content_type": self.content_type,
            "notes": list(self.notes),
            "raw_url": self.raw_url,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "MirrorRecord | None":
        if not isinstance(raw, Mapping):
            return None
        url = meta.clean_url(raw.get("url"))
        game_key = raw.get("game_key")
        if not url or not isinstance(game_key, str) or not game_key:
            return None
        return cls(
            source_id=str(raw.get("source_id") or ""),
            game_key=game_key,
            url=url,
            filename=meta.clean_text(raw.get("filename"), 260),
            size=_as_int(raw.get("size")),
            format=meta.clean_text(raw.get("format"), 40),
            checksum_algorithm=meta.clean_text(raw.get("checksum_algorithm"), 20),
            checksum_value=meta.clean_text(raw.get("checksum_value"), 200),
            version=meta.clean_text(raw.get("version"), 60),
            discovered_at=_as_float(raw.get("discovered_at")),
            last_checked=_as_float(raw.get("last_checked")),
            # A "verified" status only means something next to the file
            # classification the probe stored with it.  Records written before
            # ``url_kind`` existed (or imported with a status from the scraper)
            # carry the word alone, so they load as unchecked and get probed
            # again instead of being offered to the download manager.
            check_status=(
                _coherent_status(raw.get("check_status"), raw.get("url_kind"))
            ),
            requires_auth=bool(raw.get("requires_auth", False)),
            label=meta.clean_text(raw.get("label"), 260),
            host=meta.clean_text(raw.get("host"), 120),
            url_kind=meta.clean_text(raw.get("url_kind"), 40),
            content_type=meta.clean_text(raw.get("content_type"), 120),
            notes=tuple(
                meta.clean_text(note, 400)
                for note in raw.get("notes")
                if isinstance(note, str)
            )[:8] if isinstance(raw.get("notes"), (list, tuple)) else (),
            raw_url=str(raw.get("raw_url") or "")[:2048],
        )


@dataclass
class ImportJob:
    """Durable state for one import run of one source."""

    job_id: str = ""
    source_id: str = ""
    source_url: str = ""
    status: str = "not started"
    phase: str = "catalog"
    cursor: str | None = None
    pages_processed: int = 0
    pages_failed: int = 0
    games_discovered: int = 0
    games_imported: int = 0
    games_enriched: int = 0
    duplicates: int = 0
    skipped: int = 0
    retries: int = 0
    magnets_found: int = 0
    mirrors_found: int = 0
    total_estimate: int | None = None
    #: True when a configured record/request limit stopped the catalog walk.
    #: Enrichment must not turn that into a claim of complete coverage.
    catalog_limited: bool = False
    #: What the last request actually returned, for the diagnostics panel.
    response_format: str = ""
    pagination: str = ""
    last_error: str = ""
    #: Enrichment is a second pass, checkpointed by the last enriched key.
    enrich_cursor: str = ""
    enrich_total: int = 0
    enrich_done: int = 0
    enrich_failures: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.status in ("complete", "partial", "failed", "blocked", "interrupted")

    @property
    def resumable(self) -> bool:
        if self.status not in ("interrupted", "partial"):
            return False
        if self.phase == "enrichment":
            return self.enrich_done < self.enrich_total
        return bool(self.cursor)

    def add_error(self, message: str, *, page: int | None = None) -> None:
        if len(self.errors) >= MAX_ERRORS_PER_JOB:
            return
        self.errors.append(
            {"at": _now(), "page": page, "message": meta.clean_text(message, 400)}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "status": self.status,
            "phase": self.phase,
            "cursor": self.cursor,
            "pages_processed": self.pages_processed,
            "pages_failed": self.pages_failed,
            "games_discovered": self.games_discovered,
            "games_imported": self.games_imported,
            "games_enriched": self.games_enriched,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
            "retries": self.retries,
            "magnets_found": self.magnets_found,
            "mirrors_found": self.mirrors_found,
            "total_estimate": self.total_estimate,
            "catalog_limited": self.catalog_limited,
            "response_format": self.response_format,
            "pagination": self.pagination,
            "last_error": self.last_error,
            "enrich_cursor": self.enrich_cursor,
            "enrich_total": self.enrich_total,
            "enrich_done": self.enrich_done,
            "enrich_failures": self.enrich_failures,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ImportJob | None":
        if not isinstance(raw, Mapping):
            return None
        job_id = raw.get("job_id")
        source_id = raw.get("source_id")
        if not isinstance(job_id, str) or not job_id:
            return None
        if not isinstance(source_id, str) or not source_id:
            return None
        status = str(raw.get("status") or "")
        errors = raw.get("errors")
        return cls(
            job_id=job_id,
            source_id=source_id,
            source_url=meta.clean_url(raw.get("source_url")),
            status=status if status in JOB_STATUSES else "failed",
            phase=meta.clean_text(raw.get("phase"), 40) or "catalog",
            cursor=str(raw["cursor"]) if isinstance(raw.get("cursor"), str) else None,
            pages_processed=_as_int(raw.get("pages_processed")),
            pages_failed=_as_int(raw.get("pages_failed")),
            games_discovered=_as_int(raw.get("games_discovered")),
            games_imported=_as_int(raw.get("games_imported")),
            games_enriched=_as_int(raw.get("games_enriched")),
            duplicates=_as_int(raw.get("duplicates")),
            skipped=_as_int(raw.get("skipped")),
            retries=_as_int(raw.get("retries")),
            magnets_found=_as_int(raw.get("magnets_found")),
            mirrors_found=_as_int(raw.get("mirrors_found")),
            response_format=meta.clean_text(raw.get("response_format"), 120),
            pagination=meta.clean_text(raw.get("pagination"), 120),
            last_error=meta.clean_text(raw.get("last_error"), 400),
            enrich_cursor=meta.clean_text(raw.get("enrich_cursor"), 400),
            enrich_total=_as_int(raw.get("enrich_total")),
            enrich_done=_as_int(raw.get("enrich_done")),
            enrich_failures=_as_int(raw.get("enrich_failures")),
            total_estimate=(
                raw["total_estimate"]
                if isinstance(raw.get("total_estimate"), int)
                and not isinstance(raw.get("total_estimate"), bool)
                else None
            ),
            catalog_limited=bool(raw.get("catalog_limited", False)),
            started_at=_as_float(raw.get("started_at")),
            updated_at=_as_float(raw.get("updated_at")),
            finished_at=_as_float(raw.get("finished_at")),
            errors=[dict(e) for e in errors if isinstance(e, Mapping)] if isinstance(errors, list) else [],
        )


MAX_IMAGE_HOSTS = 16


def _clean_config(value: Any) -> dict[str, Any]:
    """Re-validate a stored mapping; a hand-edited file must not crash loading."""
    from .sources import normalize_source_config

    try:
        return normalize_source_config(value)
    except Exception:
        return {}
_HOST_PATTERN = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")


def _clean_hosts(value: Any) -> list[str]:
    """Keep short, plain hostnames only; a stored catalog cannot name a scheme."""
    if not isinstance(value, (list, tuple)):
        return []
    kept: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        host = item.strip().casefold()
        if not host or not _HOST_PATTERN.fullmatch(host) or "." not in host:
            continue
        if host not in kept:
            kept.append(host)
        if len(kept) >= MAX_IMAGE_HOSTS:
            break
    return kept


@dataclass
class SourceRecord:
    """A configured source and its last known capabilities."""

    source_id: str
    url: str
    adapter: str
    name: str = ""
    collection: str = ""
    added_at: float = 0.0
    last_sync: float = 0.0
    last_error: str = ""
    imported: int = 0
    enabled: bool = True
    capabilities: dict[str, Any] = field(default_factory=dict)
    #: Hosts this source's own metadata pointed at for artwork.  Persisted so the
    #: window can approve an imported cover without re-reading the catalog.
    image_hosts: list[str] = field(default_factory=list)
    #: User mapping for a configured JSON catalog (records path, fields, pages).
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "adapter": self.adapter,
            "name": self.name,
            "collection": self.collection,
            "added_at": self.added_at,
            "last_sync": self.last_sync,
            "last_error": self.last_error,
            "imported": self.imported,
            "enabled": self.enabled,
            "capabilities": dict(self.capabilities),
            "image_hosts": list(self.image_hosts),
            "config": dict(self.config),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SourceRecord | None":
        if not isinstance(raw, Mapping):
            return None
        source_id = raw.get("source_id")
        raw_url = raw.get("url")
        url = meta.clean_url(raw_url)
        if not url and isinstance(raw_url, str) and raw_url.startswith("file://"):
            # A local JSON/CSV catalog is a legitimate configured source; it is
            # never fetched over the network and cannot be selected by URL.
            url = raw_url.strip()
        if not isinstance(source_id, str) or not source_id or not url:
            return None
        capabilities = raw.get("capabilities")
        return cls(
            source_id=source_id,
            url=url,
            adapter=meta.clean_text(raw.get("adapter"), 60),
            name=meta.clean_text(raw.get("name"), 120),
            collection=meta.clean_text(raw.get("collection"), 120),
            added_at=_as_float(raw.get("added_at")),
            last_sync=_as_float(raw.get("last_sync")),
            last_error=meta.clean_text(raw.get("last_error"), 300),
            imported=_as_int(raw.get("imported")),
            enabled=bool(raw.get("enabled", True)),
            capabilities=dict(capabilities) if isinstance(capabilities, Mapping) else {},
            image_hosts=_clean_hosts(raw.get("image_hosts")),
            config=_clean_config(raw.get("config")),
        )


@dataclass
class UpsertResult:
    """What happened to one incoming record."""

    kind: str  # inserted | updated | duplicate | rejected
    key: str = ""
    reason: str = ""


class CatalogStore:
    """JSON-backed catalog with explicit indexes and atomic saves."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else None
        self.sources: dict[str, SourceRecord] = {}
        self.games: dict[str, CatalogGame] = {}
        self.magnets: dict[str, MagnetRecord] = {}
        self.mirrors: dict[str, MirrorRecord] = {}
        self.jobs: dict[str, ImportJob] = {}
        self._title_index: dict[tuple[str, str], list[str]] = {}
        self._source_url_index: dict[str, str] = {}
        #: ``game_key`` -> mirror dict keys / magnet infohashes, so the Browse
        #: list can ask each row about its links without walking every table.
        self._mirrors_by_game: dict[str, list[str]] = {}
        self._magnets_by_game: dict[str, list[str]] = {}
        if self.path is not None:
            self.load()

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        self.sources.clear()
        self.games.clear()
        self.magnets.clear()
        self.mirrors.clear()
        self.jobs.clear()
        if self.path is None:
            self._reindex()
            return
        data = read_json(self.path, None)
        if not isinstance(data, Mapping):
            self._reindex()
            return
        for raw in data.get("sources", []) if isinstance(data.get("sources"), list) else []:
            record = SourceRecord.from_dict(raw)
            if record is not None:
                self.sources[record.source_id] = record
        for raw in data.get("games", []) if isinstance(data.get("games"), list) else []:
            game = CatalogGame.from_dict(raw)
            if game is not None:
                self.games[game.key] = game
        for raw in data.get("magnets", []) if isinstance(data.get("magnets"), list) else []:
            record = MagnetRecord.from_dict(raw)
            if record is not None:
                self.magnets[record.infohash] = record
        for raw in data.get("mirrors", []) if isinstance(data.get("mirrors"), list) else []:
            record = MirrorRecord.from_dict(raw)
            if record is not None:
                self.mirrors[self._mirror_key(record.game_key, record.url)] = record
        for raw in data.get("jobs", []) if isinstance(data.get("jobs"), list) else []:
            job = ImportJob.from_dict(raw)
            if job is not None:
                self.jobs[job.job_id] = job
        self._reindex()

    def save(self) -> bool:
        if self.path is None:
            return False
        atomic_write_json(
            self.path,
            {
                "version": SCHEMA_VERSION,
                "saved_at": _now(),
                "sources": [r.to_dict() for r in self.sources.values()],
                "games": [g.to_dict() for g in self.games.values()],
                "magnets": [m.to_dict() for m in self.magnets.values()],
                "mirrors": [m.to_dict() for m in self.mirrors.values()],
                "jobs": [j.to_dict() for j in self.jobs.values()],
            },
        )
        return True

    def _reindex(self) -> None:
        self._title_index = {}
        self._source_url_index = {}
        for key, game in self.games.items():
            self._index_game(game)
        # Never let a hand-edited file point a magnet at a missing game.
        for ref_list in (m.refs for m in self.magnets.values()):
            for ref in ref_list:
                if ref.get("game_key") not in self.games:
                    ref["game_key"] = ""
        self._mirrors_by_game = {}
        for key, record in self.mirrors.items():
            self._game_links(self._mirrors_by_game, record.game_key, key)
        self._magnets_by_game = {}
        for infohash, record in self.magnets.items():
            for ref in record.refs:
                self._game_links(
                    self._magnets_by_game, ref.get("game_key") or "", infohash
                )

    @staticmethod
    def _game_links(index: dict[str, list[str]], game_key: str, value: str) -> None:
        """Remember one link for one game, without duplicating it."""
        if not game_key:
            return
        known = index.setdefault(game_key, [])
        if value not in known:
            known.append(value)

    def _index_game(self, game: CatalogGame) -> None:
        if game.year and game.normalized_title:
            self._title_index.setdefault((game.normalized_title, game.year), []).append(game.key)
        if game.source_url:
            self._source_url_index.setdefault(game.source_url, game.key)

    @staticmethod
    def _mirror_key(game_key: str, url: str) -> str:
        return f"{game_key}|{url}"

    # -- sources ------------------------------------------------------------

    def add_source(self, record: SourceRecord) -> SourceRecord:
        if not record.added_at:
            record.added_at = _now()
        self.sources[record.source_id] = record
        return record

    def get_source(self, source_id: str) -> SourceRecord | None:
        return self.sources.get(source_id)

    def remove_source(self, source_id: str) -> tuple[int, int]:
        """Forget a source and its records. Returns (games, jobs) removed."""
        self.sources.pop(source_id, None)
        doomed = [key for key, game in self.games.items() if game.source_id == source_id]
        for key in doomed:
            self.games.pop(key, None)
        jobs = [job_id for job_id, job in self.jobs.items() if job.source_id == source_id]
        for job_id in jobs:
            self.jobs.pop(job_id, None)
        for infohash, record in list(self.magnets.items()):
            record.refs = [r for r in record.refs if r.get("source_id") != source_id]
            if not record.refs:
                self.magnets.pop(infohash, None)
        for key, mirror in list(self.mirrors.items()):
            if mirror.source_id == source_id:
                self.mirrors.pop(key, None)
        self._reindex()
        return len(doomed), len(jobs)

    def refresh_source_count(self, source_id: str) -> int:
        record = self.sources.get(source_id)
        count = sum(1 for game in self.games.values() if game.source_id == source_id)
        if record is not None:
            record.imported = count
        return count

    # -- games --------------------------------------------------------------

    def get_game(self, key: str) -> CatalogGame | None:
        return self.games.get(key)

    def find_by_source_url(self, url: str) -> CatalogGame | None:
        key = self._source_url_index.get(url)
        return self.games.get(key) if key else None

    def find_duplicates(self, game: CatalogGame) -> list[CatalogGame]:
        if not game.normalized_title or not game.year:
            return []
        keys = self._title_index.get((game.normalized_title, game.year), [])
        return [self.games[key] for key in keys if key != game.key and key in self.games]

    def upsert_game(self, incoming: SourceGame) -> UpsertResult:
        """Insert or merge one source record without losing existing values."""
        key = f"{incoming.source_id}:{incoming.game_id}"
        if not incoming.source_id or not incoming.game_id or not incoming.title.strip():
            return UpsertResult("rejected", key, "The record has no usable identity or title.")
        cleaned = CatalogGame.from_dict(
            {
                "source_id": incoming.source_id,
                "game_id": incoming.game_id,
                "title": incoming.title,
                "year": incoming.year,
                "creator": incoming.creator,
                "developer": incoming.developer,
                "publisher": incoming.publisher,
                "release_date": incoming.release_date,
                # clean_list handles both a tuple and a comma-separated string.
                "genres": incoming.genres,
                "platforms": incoming.platforms,
                "description": incoming.description,
                "cover_url": incoming.cover_url,
                "source_url": incoming.source_url,
                "extra": dict(incoming.extra),
            }
        )
        if cleaned is None:
            return UpsertResult("rejected", key, "The record has no usable identity.")
        existing = self.games.get(key)
        now = _now()
        if existing is None:
            cleaned.first_seen = now
            cleaned.updated_at = now
            dupes = self.find_duplicates(cleaned)
            if dupes:
                cleaned.duplicate_of = dupes[0].key
            self.games[key] = cleaned
            self._index_game(cleaned)
            return UpsertResult("duplicate" if dupes else "inserted", key)

        # Merge: fill gaps, keep answers already present, record real conflicts.
        merged = False
        for name in (
            "year", "creator", "developer", "publisher", "release_date",
            "genres", "platforms", "description", "cover_url", "source_url",
        ):
            new_value = getattr(cleaned, name)
            old_value = getattr(existing, name)
            if not new_value:
                continue
            if not old_value:
                setattr(existing, name, new_value)
                merged = True
            elif old_value != new_value and name in (
                "year", "developer", "publisher", "release_date"
            ):
                existing.conflicts[name] = {
                    "kept": str(old_value),
                    "incoming": str(new_value),
                }
        if cleaned.title and cleaned.title != existing.title:
            if not existing.title:
                existing.title = cleaned.title
                merged = True
            else:
                existing.conflicts["title"] = {
                    "kept": existing.title,
                    "incoming": cleaned.title,
                }
        if cleaned.extra:
            for name, value in cleaned.extra.items():
                if name == "source_fields" and isinstance(value, Mapping):
                    # Provider fields merge key by key, so a later import that
                    # omits a field never erases what an earlier one supplied.
                    current = existing.extra.get(name)
                    merged_fields = dict(current) if isinstance(current, Mapping) else {}
                    added = False
                    for key, item in value.items():
                        if key not in merged_fields and item not in (None, ""):
                            merged_fields[key] = item
                            added = True
                    if added:
                        existing.extra[name] = merged_fields
                        merged = True
                    continue
                if name not in existing.extra:
                    existing.extra[name] = value
                    merged = True
        existing.updated_at = now
        return UpsertResult("updated" if merged else "duplicate", key)

    def upsert_games(self, incoming: Iterable[SourceGame]) -> dict[str, Any]:
        """Insert or merge a whole batch, returning tallies instead of records.

        The per-record work is the same; batching avoids one result object and
        one branch decision per record on a 30k-record page walk, and a record
        that cannot be stored is counted without killing the page.
        """
        tally: dict[str, Any] = {
            "inserted": 0, "updated": 0, "duplicate": 0, "rejected": 0,
            "errors": [],
        }
        for game in incoming:
            try:
                result = self.upsert_game(game)
            except Exception as exc:  # one bad record never kills the page
                tally["rejected"] += 1
                if len(tally["errors"]) < 5:
                    tally["errors"].append(
                        f"{getattr(game, 'game_id', '?')}: {exc}"
                    )
                continue
            tally[result.kind] = tally.get(result.kind, 0) + 1
        return tally

    def count_games(self, source_id: str | None = None) -> int:
        if source_id is None:
            return len(self.games)
        return sum(1 for game in self.games.values() if game.source_id == source_id)

    def iter_games(self, source_id: str | None = None) -> Iterable[CatalogGame]:
        for game in list(self.games.values()):
            if source_id is None or game.source_id == source_id:
                yield game

    # -- magnets ------------------------------------------------------------

    def add_magnet(self, record: MagnetRecord) -> str:
        """Store a validated magnet, de-duplicated by infohash."""
        if not record.infohash or not record.uri:
            return "rejected"
        for ref in record.refs:
            self._game_links(
                self._magnets_by_game, ref.get("game_key") or "", record.infohash
            )
        existing = self.magnets.get(record.infohash)
        if existing is None:
            self.magnets[record.infohash] = record
            return "inserted"
        changed = False
        for ref in record.refs:
            if ref and ref.get("game_key") and ref not in existing.refs:
                existing.refs.append(ref)
                changed = True
        if not existing.display_name and record.display_name:
            existing.display_name = record.display_name
            changed = True
        return "merged" if changed else "duplicate"

    def magnets_for(self, game_key: str) -> list[MagnetRecord]:
        return [
            self.magnets[infohash]
            for infohash in self._magnets_by_game.get(game_key, [])
            if infohash in self.magnets
        ]

    # -- mirrors ------------------------------------------------------------

    def add_mirror(self, record: MirrorRecord) -> str:
        if not record.url or not record.game_key:
            return "rejected"
        key = self._mirror_key(record.game_key, record.url)
        self._game_links(self._mirrors_by_game, record.game_key, key)
        existing = self.mirrors.get(key)
        if existing is None:
            self.mirrors[key] = record
            return "inserted"
        changed = False
        for name in (
            "filename", "format", "checksum_algorithm", "checksum_value",
            "version", "label", "host", "url_kind", "raw_url",
        ):
            if not getattr(existing, name) and getattr(record, name):
                setattr(existing, name, getattr(record, name))
                changed = True
        if not existing.size and record.size:
            existing.size = record.size
            changed = True
        if not existing.notes and record.notes:
            existing.notes = tuple(record.notes)
            changed = True
        return "merged" if changed else "duplicate"

    def mirrors_for(self, game_key: str) -> list[MirrorRecord]:
        return [
            self.mirrors[key]
            for key in self._mirrors_by_game.get(game_key, [])
            if key in self.mirrors
        ]

    def download_state(self, game_key: str) -> str:
        """The strongest thing actually known about a game's download links.

        ``ready`` is only returned for a mirror whose check really ran; a
        magnet keeps a game at ``unverified`` even when every mirror failed,
        because it is still a link a client can be handed.  A source that
        published no link at all is ``none``, not ``broken``.
        """
        # Mirror check_status values are their own vocabulary: only a check
        # that really ran says "verified".
        statuses = [mirror.check_status for mirror in self.mirrors_for(game_key)]
        if "verified" in statuses:
            return DOWNLOAD_READY
        if "unchecked" in statuses or self.magnets_for(game_key):
            return DOWNLOAD_UNVERIFIED
        return DOWNLOAD_BROKEN if statuses else DOWNLOAD_NONE

    def estimated_size(self, game_key: str) -> int | None:
        """The best known size in bytes: a mirror's own, else the source's text.

        Display only - a size never gates a download.  A mirror's byte count
        wins because a response measured it; a provider string such as
        ``"7.69 GB"`` is whatever the catalog said, so it is parsed only when
        it is unambiguous and refused otherwise.
        """
        sizes = [m.size for m in self.mirrors_for(game_key) if m.size > 0]
        if sizes:
            return max(sizes)
        game = self.games.get(game_key)
        if game is None or not isinstance(game.extra, Mapping):
            return None
        fields = game.extra.get("source_fields")
        for table in (fields, game.extra):
            if not isinstance(table, Mapping):
                continue
            for name in _SIZE_FIELDS:
                size = parse_size(table.get(name))
                if size:
                    return size
        return None

    # -- jobs ---------------------------------------------------------------

    def start_job(
        self,
        source_id: str,
        source_url: str,
        *,
        resume: ImportJob | None = None,
    ) -> ImportJob:
        if resume is not None:
            resume.status = "importing"
            resume.updated_at = _now()
            self.jobs[resume.job_id] = resume
            return resume
        job = ImportJob(
            job_id=uuid.uuid4().hex[:12],
            source_id=source_id,
            source_url=source_url,
            status="importing",
            started_at=_now(),
            updated_at=_now(),
        )
        self.jobs[job.job_id] = job
        return job

    def save_job(self, job: ImportJob) -> None:
        job.updated_at = _now()
        self.jobs[job.job_id] = job

    def get_job(self, job_id: str) -> ImportJob | None:
        return self.jobs.get(job_id)

    def latest_job(self, source_id: str) -> ImportJob | None:
        candidates = [j for j in self.jobs.values() if j.source_id == source_id]
        if not candidates:
            return None
        return max(candidates, key=lambda job: (job.started_at, job.updated_at))

    def jobs(self) -> list[ImportJob]:
        return sorted(
            self.jobs.values(), key=lambda job: (-job.started_at, job.job_id)
        )

    # -- reporting ----------------------------------------------------------

    def source_summary(self, source_id: str) -> dict[str, Any]:
        """Honest counters for one source's discovered catalog.

        ``downloadable`` is only incremented by a mirror check that actually
        observed a file response; a listed record, a record with links, and a
        verified download are three different facts.
        """
        games = list(self.iter_games(source_id))
        with_links = 0
        without_links = 0
        link_count = 0
        checked = 0
        verified = 0
        pages_seen = 0
        failed = 0
        metadata = 0
        for game in games:
            mirrors = self.mirrors_for(game.key)
            if mirrors:
                with_links += 1
            else:
                without_links += 1
            link_count += len(mirrors)
            for mirror in mirrors:
                if mirror.check_status in (
                    "verified", "page", "missing", "failed", "blocked",
                ):
                    checked += 1
                if mirror.check_status == "verified" and mirror.url_kind == "file":
                    verified += 1
            state = game.extra.get("details_state")
            fields = game.extra.get("source_fields")
            if state == "fetched" or (
                game.title
                and (
                    game.developer or game.description or game.cover_url
                    or (isinstance(fields, Mapping) and fields)
                )
            ):
                metadata += 1
            kryoto_info = game.extra.get("kryoto")
            if isinstance(kryoto_info, Mapping) and kryoto_info.get("links"):
                pages_seen += 1
        job = self.latest_job(source_id)
        source = self.sources.get(source_id)
        if job is not None:
            # Skipped records are logged as job errors but are not failed
            # requests; only page and enrichment failures belong here.
            failed = job.pages_failed + job.enrich_failures
        return {
            "source_id": source_id,
            "games_discovered": len(games),
            "metadata_retrieved": metadata,
            "games_with_links": with_links,
            "games_without_links": without_links,
            "links_listed": link_count,
            "links_checked": checked,
            "links_verified": verified,
            "failed_requests": failed,
            "last_refresh": source.last_sync if source is not None else 0.0,
            "last_error": source.last_error if source is not None else "",
            "job_status": job.status if job is not None else "not started",
            "pagination_complete": bool(job is not None and job.status == "complete"),
            "pages_seen": pages_seen,
        }

    def stats(self) -> dict[str, Any]:
        games = list(self.games.values())
        sources = {sid: 0 for sid in self.sources}
        for game in games:
            sources[game.source_id] = sources.get(game.source_id, 0) + 1
        return {
            "sources": len(self.sources),
            "games": len(games),
            "duplicates": sum(1 for game in games if game.duplicate_of),
            "with_artwork": sum(1 for game in games if game.cover_url),
            "with_description": sum(1 for game in games if game.description),
            "magnets": len(self.magnets),
            "mirrors": len(self.mirrors),
            "games_per_source": sources,
            "jobs": len(self.jobs),
        }


__all__ = [
    "CatalogGame",
    "CatalogStore",
    "DOWNLOAD_BROKEN",
    "DOWNLOAD_NONE",
    "DOWNLOAD_READY",
    "DOWNLOAD_STATES",
    "DOWNLOAD_STATE_LABELS",
    "DOWNLOAD_STATE_RANK",
    "DOWNLOAD_UNVERIFIED",
    "ImportJob",
    "JOB_STATUSES",
    "MagnetRecord",
    "MirrorRecord",
    "SCHEMA_VERSION",
    "SourceRecord",
    "UpsertResult",
]
