"""Find a magnet link published in a small JSON sidecar a source points at.

A catalog record sometimes names a second, machine-readable document beside the
game (``.../game.json``).  That document may list a magnet URI or a bare torrent
infohash, and this module reads exactly that one document and returns a
validated :class:`~lgl.magnet.MagnetLink`.

Three rules hold, and they are the reason this lives in its own module:

* Only an adapter that declares it may distribute files
  (``download_capability`` of ``public-files`` or ``authorized-files``) can ask
  for a sidecar.  A store-page catalog, a local file, and a mapped endpoint all
  declare ``none``/``store-links``, so reading a sidecar can never become a way
  around a source's own permissions.
* Nothing is invented.  A magnet URI must parse, and a bare value is only used
  when the key it sits under names an infohash - a ``sha1``/``md5`` field is
  never treated as one - and then it must validate before
  :func:`~lgl.magnet.build_magnet` will build a URI from it.
* Every outcome is a status value (``magnet``, ``infohash``, ``none``,
  ``unsupported``, ``error``) with a reason, never an exception the caller has
  to interpret, and the caller stores the result so one URL is read once.

The module performs no torrent work: no tracker is contacted, no info dictionary
is fetched, and no claim is made that the content behind a link exists, is
complete, or is licensed.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Mapping

from . import __version__
from . import magnet as magnetlib
from .util import AppError

USER_AGENT = f"linux-game-library/{__version__} (personal desktop client)"
#: A sidecar is a small document; anything larger is not one.
MAX_SIDECAR_BYTES = 512 * 1024
DEFAULT_TIMEOUT = 15.0
#: The only source permissions that may read a sidecar for file links.
PERMITTED_CAPABILITIES = frozenset({"public-files", "authorized-files"})
#: Keys that say "this value is a torrent infohash".  ``hash`` is not here on
#: purpose: it is far more often a file checksum than an infohash.
INFOHASH_KEYS = frozenset({"infohash", "info_hash", "infohash_v1", "btih"})
MAX_DEPTH = 6

STATUS_MAGNET = "magnet"
STATUS_INFOHASH = "infohash"
STATUS_NONE = "none"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"


class SidecarError(AppError):
    """A sidecar document could not be read."""


@dataclass(frozen=True)
class SidecarResult:
    """What one sidecar JSON said, and why nothing was found when nothing was."""

    status: str
    url: str = ""
    link: magnetlib.MagnetLink | None = None
    reason: str = ""
    checked_at: float = 0.0

    @property
    def found(self) -> bool:
        return self.link is not None and self.status in (STATUS_MAGNET, STATUS_INFOHASH)

    @property
    def done(self) -> bool:
        """True when the URL has been answered and should not be read again."""
        return self.status != STATUS_ERROR

    def describe(self) -> str:
        if self.status == STATUS_MAGNET:
            return "A magnet link was published in the source's JSON sidecar."
        if self.status == STATUS_INFOHASH:
            return (
                "The source published a torrent infohash; the magnet link was "
                "built from it."
            )
        if self.status == STATUS_NONE:
            return self.reason or "The JSON sidecar carries no magnet or infohash."
        if self.status == STATUS_UNSUPPORTED:
            return self.reason or "This source may not be read for file links."
        return self.reason or "The JSON sidecar could not be read."

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "url": self.url,
            "reason": self.reason,
            "checked_at": self.checked_at,
            "infohash": self.link.infohash if self.link else "",
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SidecarResult | None":
        if not isinstance(raw, Mapping):
            return None
        result = cls(
            status=str(raw.get("status") or ""),
            url=str(raw.get("url") or ""),
            reason=str(raw.get("reason") or ""),
            checked_at=float(raw.get("checked_at") or 0.0),
        )
        return result if result.status else None


def is_json_url(value: Any) -> bool:
    """True for a plain ``https://`` URL whose path ends in ``.json``."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 2048:
        return False
    try:
        split = urllib.parse.urlsplit(text)
    except ValueError:
        return False
    if split.scheme != "https" or not split.hostname:
        return False
    if split.username or split.password:
        return False
    if any(char.isspace() for char in text):
        return False
    return split.path.lower().endswith(".json")


def json_urls(values: Iterable[Any]) -> list[str]:
    """Every distinct ``.json`` URL in *values*, in the order they appear."""
    found: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            candidates: Iterable[Any] = value
        else:
            candidates = (value,)
        for candidate in candidates:
            if is_json_url(candidate) and candidate not in found:
                found.append(candidate.strip())
    return found


def host_allowed(url: str, allowed_hosts: Iterable[str]) -> bool:
    """True when *url*'s host is one of *allowed_hosts* or a subdomain of one."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host or host.replace(".", "").isdigit():
        return False
    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in (str(item).lower() for item in allowed_hosts if item)
    )


def _fetch(url: str, timeout: float, max_bytes: int) -> bytes:
    """Read one sidecar with a timeout and a hard size cap."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain;q=0.5",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            # A redirect that ends somewhere else must not be followed blindly.
            final = getattr(response, "geturl", lambda: url)()
            if final and final != url and not is_json_url(final):
                raise SidecarError(
                    "The sidecar URL redirected somewhere that is not HTTPS JSON."
                )
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise SidecarError(f"The sidecar returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SidecarError(f"The sidecar could not be read: {exc}") from exc
    if len(data) > max_bytes:
        raise SidecarError(
            f"The document is larger than {max_bytes // 1024} KiB, so it was not parsed."
        )
    return data


def _pairs(value: Any, depth: int = 0) -> Iterator[tuple[str, Any]]:
    """Yield ``(key, value)`` for every scalar under *value*, bounded by depth."""
    if depth > MAX_DEPTH:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).strip().casefold()
            if isinstance(item, (Mapping, list, tuple)):
                yield from _pairs(item, depth + 1)
            else:
                yield name, item
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (Mapping, list, tuple)):
                yield from _pairs(item, depth + 1)


def find_magnet_in_document(document: Any, *, title: str = "") -> SidecarResult:
    """Return the first usable magnet link in an already-parsed document.

    A published magnet URI always wins over a bare infohash, because it carries
    the trackers and name the source chose.  A bare value is only considered
    under an infohash key, and is validated before a URI is built from it.
    """
    rejected: str = ""
    for _key, value in _pairs(document):
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text.lower().startswith("magnet:?"):
            continue
        try:
            link = magnetlib.parse_magnet(text)
        except magnetlib.MagnetError as exc:
            rejected = str(exc)
            continue
        return SidecarResult(
            status=STATUS_MAGNET, link=link, checked_at=time.time()
        )

    for key, value in _pairs(document):
        if key not in INFOHASH_KEYS or not isinstance(value, str):
            continue
        infohash = magnetlib.normalize_infohash(value)
        if not infohash:
            rejected = "The published infohash is not a valid 160-bit hash."
            continue
        try:
            link = magnetlib.build_magnet(infohash, display_name=title)
        except magnetlib.MagnetError as exc:  # pragma: no cover - guarded above
            rejected = str(exc)
            continue
        return SidecarResult(
            status=STATUS_INFOHASH, link=link, checked_at=time.time()
        )

    reason = "The JSON sidecar carries no magnet link or infohash."
    if rejected:
        reason = f"{reason} ({rejected})"
    return SidecarResult(status=STATUS_NONE, reason=reason, checked_at=time.time())


def discover_magnet(
    url: str,
    *,
    allowed_hosts: Iterable[str],
    download_capability: str,
    title: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = MAX_SIDECAR_BYTES,
    fetcher: Callable[[str, float, int], bytes] | None = None,
) -> SidecarResult:
    """Read one JSON sidecar and return the magnet link it publishes, if any.

    ``fetcher`` is injected by tests; the default performs one bounded HTTPS
    request.  Nothing here raises for a missing, unreachable, oversized, or
    unrelated document: the result says which of those happened.
    """
    capability = str(download_capability or "none")
    if capability not in PERMITTED_CAPABILITIES:
        return SidecarResult(
            status=STATUS_UNSUPPORTED,
            url=url if is_json_url(url) else "",
            reason=(
                "This source declares that it does not distribute files "
                f"({capability}), so its sidecar JSON is not read for magnets."
            ),
            checked_at=time.time(),
        )
    if not is_json_url(url):
        return SidecarResult(
            status=STATUS_UNSUPPORTED,
            url="",
            reason="Only an HTTPS URL ending in .json is read as a sidecar.",
            checked_at=time.time(),
        )
    if not host_allowed(url, allowed_hosts):
        return SidecarResult(
            status=STATUS_UNSUPPORTED,
            url=url,
            reason=(
                "The sidecar is not on a host this source declares, so it is not "
                "requested."
            ),
            checked_at=time.time(),
        )

    read = fetcher or _fetch
    try:
        payload = read(url, timeout, max_bytes)
    except SidecarError as exc:
        return SidecarResult(status=STATUS_ERROR, url=url, reason=str(exc), checked_at=time.time())
    except Exception as exc:  # a broken fetcher never breaks an import
        return SidecarResult(
            status=STATUS_ERROR, url=url, reason=f"The sidecar could not be read: {exc}",
            checked_at=time.time(),
        )
    if len(payload) > max_bytes:
        return SidecarResult(
            status=STATUS_ERROR,
            url=url,
            reason=f"The document is larger than {max_bytes // 1024} KiB, so it was not parsed.",
            checked_at=time.time(),
        )
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        return SidecarResult(
            status=STATUS_ERROR, url=url, reason=f"The document is not valid JSON: {exc}",
            checked_at=time.time(),
        )

    found = find_magnet_in_document(document, title=title)
    return SidecarResult(
        status=found.status,
        url=url,
        link=found.link,
        reason=found.reason,
        checked_at=found.checked_at,
    )


__all__ = [
    "DEFAULT_TIMEOUT",
    "INFOHASH_KEYS",
    "MAX_SIDECAR_BYTES",
    "PERMITTED_CAPABILITIES",
    "STATUS_ERROR",
    "STATUS_INFOHASH",
    "STATUS_MAGNET",
    "STATUS_NONE",
    "STATUS_UNSUPPORTED",
    "SidecarError",
    "SidecarResult",
    "discover_magnet",
    "find_magnet_in_document",
    "host_allowed",
    "is_json_url",
    "json_urls",
]
