"""Opt-in watch-history synchronisation.

Roadmap section 9 says: evaluate services before choosing one, make sync opt-in,
show status and errors, avoid duplicate entries, cope with conflicts and offline
use, and never upload history without authorisation.

There is deliberately no bundled third-party account integration here.  What
ships is the machinery every such integration needs — explicit consent, a
deduplicating merge, a durable outbox, and honest status — plus a local-file
adapter used for tests and for exporting history.  A real service plugs in by
implementing :class:`Adapter`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

SETTINGS_KEY = "history_sync"
OUTBOX_LIMIT = 500
MAX_ENTRIES = 2000
IDLE = "idle"
SYNCING = "syncing"
OK = "ok"
OFFLINE = "offline"
ERROR = "error"
DISABLED = "disabled"
NO_SERVICE = "no-service"


class SyncError(Exception):
    """A sync attempt that could not complete."""


class Adapter(Protocol):
    """What a history service has to provide."""

    name: str
    label: str
    requires_token: bool

    def push(self, entries: Sequence[Mapping[str, Any]]) -> int: ...

    def pull(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    service: str = ""
    last_sync: float = 0.0

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.service)


@dataclass
class SyncResult:
    status: str
    pushed: int = 0
    pulled: int = 0
    merged: int = 0
    conflicts: int = 0
    message: str = ""
    at: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == OK


def _clean(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def config(settings: Any) -> Config:
    raw = settings.get(SETTINGS_KEY) if settings is not None else None
    if not isinstance(raw, Mapping):
        return Config()
    try:
        last = float(raw.get("last_sync") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    return Config(
        enabled=raw.get("enabled") is True,
        service=_clean(raw.get("service"), 40),
        last_sync=last,
    )


def _write(settings: Any, cfg: Config) -> None:
    payload = {"enabled": cfg.enabled, "service": cfg.service, "last_sync": cfg.last_sync}
    settings.set(SETTINGS_KEY, payload)


def storable_token(settings: Any, token: Any) -> bool:
    """Tokens never live in the settings file; they belong in the keyring."""
    return not _clean(token)


def enable(settings: Any, service: str, *, consent: bool) -> bool:
    """Turn sync on. Refuses without explicit consent or a known service."""
    if not consent or not _clean(service):
        return False
    _write(settings, Config(enabled=True, service=_clean(service), last_sync=config(settings).last_sync))
    return True


def disable(settings: Any) -> None:
    current = config(settings)
    _write(settings, Config(enabled=False, service=current.service, last_sync=current.last_sync))


def status_line(cfg: Config) -> str:
    if not cfg.enabled:
        return "History sync is off."
    if not cfg.service:
        return "History sync is on but no service is chosen."
    if not cfg.last_sync:
        return f"History sync is on for {cfg.service}; it has not run yet."
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(cfg.last_sync))
    return f"History sync is on for {cfg.service}; last ran {when}."


def _key(entry: Mapping[str, Any]) -> str:
    kind = _clean(entry.get("kind") or entry.get("type"), 20).lower()
    title = _clean(entry.get("title") or entry.get("key"), 200).lower()
    season = _clean(entry.get("season"), 8)
    episode = _clean(entry.get("episode"), 8)
    return "|".join(part for part in (kind, title, season, episode) if part)


def _stamp(entry: Mapping[str, Any]) -> float:
    for name in ("watched_at", "updated", "timestamp", "at"):
        value = entry.get(name)
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if stamp > 0:
            return stamp
    return 0.0


def merge(
    local: Iterable[Mapping[str, Any]],
    remote: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Combine histories, newest record winning per title/episode.

    Returns the merged list and the number of entries where both sides had a
    record that disagreed.
    """
    merged: dict[str, dict[str, Any]] = {}
    conflicts = 0
    for source in (local, remote):
        for raw in source:
            if not isinstance(raw, Mapping):
                continue
            entry = dict(raw)
            key = _key(entry)
            if not key:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = entry
                continue
            if _stamp(entry) > _stamp(existing):
                if _stamp(existing) > 0 or _stamp(entry) > 0:
                    conflicts += 1
                merged[key] = entry
            elif _stamp(existing) > 0 and _stamp(entry) > 0 and _stamp(entry) != _stamp(existing):
                conflicts += 1
    return list(merged.values()), conflicts


class Outbox:
    """Records waiting to be uploaded, kept on disk so offline use is safe."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._items: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, Mapping) and _key(item):
                self._items.append(dict(item))
        del self._items[OUTBOX_LIMIT:]

    def add(self, entries: Iterable[Mapping[str, Any]]) -> int:
        known = {_key(item) for item in self._items}
        added = 0
        for raw in entries:
            if not isinstance(raw, Mapping):
                continue
            entry = dict(raw)
            key = _key(entry)
            if not key or key in known:
                continue
            if len(self._items) >= OUTBOX_LIMIT:
                break
            self._items.append(entry)
            known.add(key)
            added += 1
        if added:
            self.save()
        return added

    def pending(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]

    def acknowledge(self, entries: Iterable[Mapping[str, Any]]) -> int:
        done = {_key(item) for item in entries if isinstance(item, Mapping)}
        before = len(self._items)
        self._items = [item for item in self._items if _key(item) not in done]
        removed = before - len(self._items)
        if removed:
            self.save()
        return removed

    def clear(self) -> None:
        self._items.clear()
        try:
            self.path.unlink()
        except OSError:
            pass

    def __len__(self) -> int:
        return len(self._items)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "items": self._items[:OUTBOX_LIMIT]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
        tmp.replace(self.path)


class LocalFileAdapter:
    """Export/import history through a JSON file the user picked."""

    name = "local-file"
    label = "Local file"
    requires_token = False

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def push(self, entries: Sequence[Mapping[str, Any]]) -> int:
        existing: list[dict[str, Any]] = []
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                existing = [dict(e) for e in raw["entries"] if isinstance(e, Mapping)]
            elif isinstance(raw, list):
                existing = [dict(e) for e in raw if isinstance(e, Mapping)]
        except (OSError, ValueError):
            existing = []
        combined, _ = merge(existing, entries)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"version": 1, "entries": combined[:MAX_ENTRIES]}, indent=2), "utf-8")
        tmp.replace(self.path)
        return len(entries)

    def pull(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(raw, dict):
            items = raw.get("entries")
        else:
            items = raw
        if not isinstance(items, list):
            return []
        return [dict(e) for e in items if isinstance(e, Mapping)][:MAX_ENTRIES]


class Syncer:
    """Runs one sync pass against whichever adapter is configured."""

    def __init__(
        self,
        settings: Any,
        outbox: Outbox,
        *,
        adapters: Mapping[str, Adapter] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.outbox = outbox
        self.adapters = dict(adapters or {})
        self._clock = clock

    def run(self, local: Iterable[Mapping[str, Any]] = ()) -> SyncResult:
        cfg = config(self.settings)
        if not cfg.enabled:
            return SyncResult(status=DISABLED, message=status_line(cfg), at=self._clock())
        if not cfg.service:
            return SyncResult(status=NO_SERVICE, message=status_line(cfg), at=self._clock())
        adapter = self.adapters.get(cfg.service)
        if adapter is None:
            return SyncResult(
                status=ERROR,
                message=f"No adapter is installed for {cfg.service}.",
                at=self._clock(),
            )
        queued = self.outbox.add(local)
        pending = self.outbox.pending()
        try:
            pushed = adapter.push(pending) if pending else 0
            remote = adapter.pull()
        except Exception as exc:  # network, auth, disk — all the same to the UI
            kind = OFFLINE if "offline" in str(exc).lower() or "connect" in str(exc).lower() else ERROR
            return SyncResult(
                status=kind,
                message=f"Sync stopped: {exc}",
                at=self._clock(),
            )
        merged, conflicts = merge(self.outbox.pending(), remote)
        if pushed:
            self.outbox.acknowledge(pending)
        cfg = Config(enabled=cfg.enabled, service=cfg.service, last_sync=self._clock())
        _write(self.settings, cfg)
        return SyncResult(
            status=OK,
            pushed=pushed,
            pulled=len(remote),
            merged=len(merged),
            conflicts=conflicts,
            message=f"Synced {pushed} record(s), received {len(remote)}.",
            at=cfg.last_sync,
        )


__all__ = [
    "Adapter",
    "Config",
    "DISABLED",
    "ERROR",
    "IDLE",
    "LocalFileAdapter",
    "NO_SERVICE",
    "OFFLINE",
    "OK",
    "OUTBOX_LIMIT",
    "Outbox",
    "SETTINGS_KEY",
    "SYNCING",
    "SyncError",
    "SyncResult",
    "Syncer",
    "config",
    "disable",
    "enable",
    "merge",
    "status_line",
    "storable_token",
]
