"""Local metadata and artwork cache.

Covers roadmap section 3: cache posters locally, keep placeholders for missing
artwork, stop refetching unchanged metadata, let a wrong match be corrected by
hand, survive rate limits and timeouts, and keep provider payloads in their own
namespace instead of merging them into the app's content model.

Every entry is stored as ``{"provider", "id", "fetched", "payload"}``; callers
only ever receive the payload, so two providers cannot overwrite each other's
fields.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import platform_support

CACHE_VERSION = 1
DEFAULT_TTL = 7 * 24 * 60 * 60
MAX_ENTRY_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 600
MAX_OVERRIDES = 500
ERROR_TTL = 15 * 60

RATE_LIMIT = "rate-limit"
TIMEOUT = "timeout"
OFFLINE = "offline"
NOT_FOUND = "not-found"
AUTH = "auth"
SERVER = "server"
UNKNOWN = "unknown"

_MESSAGES = {
    RATE_LIMIT: "The metadata service is rate limiting us. We will back off and try later.",
    TIMEOUT: "The metadata service took too long to answer. Cached data stays available.",
    OFFLINE: "You appear to be offline. Showing cached metadata.",
    NOT_FOUND: "That title was not found on this provider.",
    AUTH: "The provider rejected our credentials. Check the key in Settings.",
    SERVER: "The metadata service is having problems. Showing what we already have.",
    UNKNOWN: "Something went wrong talking to the provider.",
}

#: Backoff in seconds per failure kind, indexed by attempt (0-based).
_BACKOFF = {
    RATE_LIMIT: (60, 300, 900),
    TIMEOUT: (5, 30, 120),
    OFFLINE: (15, 60, 300),
    SERVER: (30, 120, 600),
    AUTH: (600, 600, 600),
    NOT_FOUND: (3600, 3600, 3600),
    UNKNOWN: (30, 120, 300),
}


def default_root() -> Path:
    return platform_support.cache_dir() / "metadata"


def classify_error(error: Any) -> str:
    """Map a status code, exception or message onto a coarse failure kind."""
    status = getattr(error, "status", None) or getattr(error, "code", None)
    text = str(error or "").lower()
    if isinstance(status, int):
        if status == 429:
            return RATE_LIMIT
        if status in (401, 403):
            return AUTH
        if status == 404:
            return NOT_FOUND
        if 500 <= status <= 599:
            return SERVER
    name = type(error).__name__.lower() if error is not None else ""
    if isinstance(error, TimeoutError) or "timeout" in name or "timed out" in text:
        return TIMEOUT
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return RATE_LIMIT
    if "offline" in text or "name or service not known" in text or "temporary failure" in text:
        return OFFLINE
    if "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return AUTH
    if "404" in text or "not found" in text:
        return NOT_FOUND
    if "urm" in text or "refused" in text or "connection" in text:
        return OFFLINE
    if "500" in text or "502" in text or "503" in text:
        return SERVER
    return UNKNOWN


def user_message(kind: str) -> str:
    return _MESSAGES.get(kind, _MESSAGES[UNKNOWN])


def retry_delay(kind: str, attempt: int) -> int:
    steps = _BACKOFF.get(kind, _BACKOFF[UNKNOWN])
    index = max(0, min(int(attempt), len(steps) - 1))
    return steps[index]


def placeholder_color(seed: Any) -> str:
    """A stable colour for a title without artwork."""
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") % 360
    return f"hsl({hue}, 32%, 34%)"


def placeholder_svg(seed: Any, label: str = "", *, size: int = 200) -> bytes:
    """Deterministic placeholder art, so a missing poster never breaks layout."""
    initials = "".join(part[0] for part in str(label or seed).split()[:2]).upper() or "?"
    safe = (
        str(initials)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    colour = placeholder_color(seed)
    side = max(48, min(int(size), 1024))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" '
        f'viewBox="0 0 {side} {side}" role="img" aria-label="No artwork">'
        f'<rect width="{side}" height="{side}" fill="{colour}"/>'
        f'<text x="50%" y="50%" fill="#f2f2f2" font-family="sans-serif" '
        f'font-size="{side // 3}" text-anchor="middle" dominant-baseline="central">{safe}</text>'
        "</svg>"
    )
    return svg.encode("utf-8")


@dataclass(frozen=True)
class Override:
    app_key: str
    provider: str
    provider_id: str
    title: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "app_key": self.app_key,
            "provider": self.provider,
            "provider_id": self.provider_id,
            "title": self.title,
        }


class MatchOverrides:
    """Manual corrections to automatic title matches."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._items: dict[str, Override] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.get("overrides", {}).items():
            item = self._parse(key, value)
            if item is not None:
                self._items[key] = item

    @staticmethod
    def _parse(key: Any, value: Any) -> Override | None:
        if not isinstance(value, Mapping):
            return None
        provider = str(value.get("provider") or "").strip()
        provider_id = str(value.get("provider_id") or "").strip()
        clean_key = str(key or "").strip()
        if not (clean_key and provider and provider_id):
            return None
        return Override(
            app_key=clean_key[:200],
            provider=provider[:40],
            provider_id=provider_id[:120],
            title=str(value.get("title") or "")[:200],
        )

    def get(self, app_key: str) -> Override | None:
        return self._items.get(str(app_key or "").strip())

    def set(self, app_key: str, provider: str, provider_id: str, *, title: str = "") -> bool:
        key = str(app_key or "").strip()
        if not key or not str(provider).strip() or not str(provider_id).strip():
            return False
        if key not in self._items and len(self._items) >= MAX_OVERRIDES:
            return False
        self._items[key] = Override(
            app_key=key[:200],
            provider=str(provider).strip()[:40],
            provider_id=str(provider_id).strip()[:120],
            title=str(title or "")[:200],
        )
        self.save()
        return True

    def remove(self, app_key: str) -> bool:
        key = str(app_key or "").strip()
        if self._items.pop(key, None) is None:
            return False
        self.save()
        return True

    def resolve(self, app_key: str, candidates: Iterable[Mapping[str, Any]]) -> str | None:
        """Pick the provider id to use: the manual override wins."""
        override = self.get(app_key)
        if override is not None:
            return override.provider_id
        for candidate in candidates:
            provider_id = str(candidate.get("id") or candidate.get("provider_id") or "").strip()
            if provider_id:
                return provider_id
        return None

    def items(self) -> list[Override]:
        return list(self._items.values())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "overrides": {k: v.as_dict() for k, v in self._items.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
        tmp.replace(self.path)


class MetadataCache:
    """Disk cache for provider payloads and artwork bytes."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        ttl: int = DEFAULT_TTL,
        clock: Callable[[], float] = time.time,
    ):
        self.root = Path(root) if root is not None else default_root()
        self.ttl = max(0, int(ttl))
        self._clock = clock
        self._errors: dict[str, tuple[str, float, int]] = {}

    # -- paths ---------------------------------------------------------
    def _entry_path(self, provider: str, ident: str) -> Path:
        provider_slug = "".join(ch for ch in provider if ch.isalnum() or ch in "-_")[:40] or "unknown"
        digest = hashlib.sha256(f"{provider}\0{ident}".encode("utf-8")).hexdigest()[:32]
        return self.root / provider_slug / f"{digest}.json"

    def artwork_path(self, url: str | None) -> Path:
        digest = hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()
        suffix = ".img"
        lowered = str(url or "").lower().split("?")[0]
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"):
            if lowered.endswith(ext):
                suffix = ext
                break
        return self.root / "artwork" / f"{digest[:2]}" / f"{digest[2:]}{suffix}"

    # -- metadata ------------------------------------------------------
    def get(self, provider: str, ident: str) -> dict[str, Any] | None:
        path = self._entry_path(provider, ident)
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        if raw.get("provider") != provider or str(raw.get("id")) != str(ident):
            return None
        fetched = raw.get("fetched")
        if not isinstance(fetched, (int, float)):
            return None
        if self.ttl and self._clock() - float(fetched) > self.ttl:
            return None
        payload = raw.get("payload")
        return payload if isinstance(payload, dict) else None

    def is_fresh(self, provider: str, ident: str) -> bool:
        return self.get(provider, ident) is not None

    def put(self, provider: str, ident: str, payload: Mapping[str, Any]) -> bool:
        if not isinstance(payload, Mapping) or not provider or not str(ident):
            return False
        document = {
            "version": CACHE_VERSION,
            "provider": provider,
            "id": str(ident),
            "fetched": self._clock(),
            "payload": dict(payload),
        }
        encoded = json.dumps(document, sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_ENTRY_BYTES:
            return False
        path = self._entry_path(provider, ident)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(encoded)
            tmp.replace(path)
        except OSError:
            return False
        return True

    def invalidate(self, provider: str, ident: str) -> bool:
        try:
            self._entry_path(provider, ident).unlink()
        except OSError:
            return False
        return True

    # -- artwork -------------------------------------------------------
    def stored_artwork(self, url: str | None) -> Path | None:
        if not url:
            return None
        path = self.artwork_path(url)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def store_artwork(self, url: str, data: bytes) -> Path | None:
        if not url or not isinstance(data, (bytes, bytearray)) or not data:
            return None
        if len(data) > MAX_ENTRY_BYTES:
            return None
        path = self.artwork_path(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(bytes(data))
            tmp.replace(path)
        except OSError:
            return None
        return path

    # -- failures ------------------------------------------------------
    def record_error(self, provider: str, error: Any) -> str:
        kind = classify_error(error)
        previous = self._errors.get(provider)
        attempts = (previous[2] + 1) if previous and previous[0] == kind else 1
        self._errors[provider] = (kind, self._clock(), attempts)
        return kind

    def last_error(self, provider: str) -> str | None:
        entry = self._errors.get(provider)
        if entry is None:
            return None
        kind, at, _ = entry
        if self._clock() - at > ERROR_TTL:
            return None
        return kind

    def should_fetch(self, provider: str) -> bool:
        """False while a provider is backing off after a failure."""
        entry = self._errors.get(provider)
        if entry is None:
            return True
        kind, at, attempts = entry
        return self._clock() - at >= retry_delay(kind, attempts - 1)

    def clear_errors(self) -> None:
        self._errors.clear()

    # -- housekeeping ---------------------------------------------------
    def prune(self) -> int:
        removed = 0
        entries: list[tuple[float, Path]] = []
        if self.root.is_dir():
            for path in self.root.rglob("*.json"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries.append((stat.st_mtime, path))
        keep = sorted(entries, reverse=True)[:MAX_ENTRIES]
        kept = {path for _, path in keep}
        for _, path in entries:
            if path in kept and not self._expired(path):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _expired(self, path: Path) -> bool:
        if not self.ttl:
            return False
        try:
            raw = json.loads(path.read_text("utf-8"))
            fetched = float(raw.get("fetched", 0))
        except (OSError, ValueError, TypeError):
            return True
        return self._clock() - fetched > self.ttl

    def stats(self) -> dict[str, int]:
        count = 0
        size = 0
        if self.root.is_dir():
            for path in self.root.rglob("*"):
                if path.is_file():
                    count += 1
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
        return {"files": count, "bytes": size}

    def clear(self) -> int:
        removed = 0
        if self.root.is_dir():
            for path in sorted(self.root.rglob("*"), reverse=True):
                try:
                    if path.is_file():
                        path.unlink()
                        removed += 1
                    else:
                        path.rmdir()
                except OSError:
                    continue
        return removed


__all__ = [
    "AUTH",
    "CACHE_VERSION",
    "DEFAULT_TTL",
    "MatchOverrides",
    "MetadataCache",
    "NOT_FOUND",
    "OFFLINE",
    "Override",
    "RATE_LIMIT",
    "SERVER",
    "TIMEOUT",
    "UNKNOWN",
    "classify_error",
    "default_root",
    "placeholder_color",
    "placeholder_svg",
    "retry_delay",
    "user_message",
]
