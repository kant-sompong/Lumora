"""Magnet URI handling: parse, validate, and (only from a real infohash) build.

Magnet links are *optional* metadata for a library entry.  Nothing here talks
to the network, resolves a torrent, or looks up an infohash anywhere: the module
only understands the text of a ``magnet:`` URI that the user pasted, or one that
was already published on a page.  A syntactically valid magnet URI proves
nothing about whether the content behind it is reachable, complete, healthy, or
licensed, so every caller is expected to present it as unverified.

Two distinct origins are tracked, and never conflated:

``extracted``
    The URI came from somewhere else (the user's clipboard, a page they chose to
    read) and was parsed and normalized here.
``constructed``
    Only the bare infohash was known and this module built a standard magnet
    URI around it.  It says nothing about trackers, seeds, or the name.

Never invent an infohash.  :func:`build_magnet` refuses anything that is not a
valid 160-bit hash.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, quote, urlsplit

from .util import AppError

#: A magnet URI longer than this is treated as junk rather than stored.
MAX_MAGNET_LENGTH = 4096
#: Longest display name kept from ``dn=``.
MAX_NAME_LENGTH = 300
#: How many ``tr=`` trackers are kept.
MAX_TRACKERS = 32

ORIGIN_EXTRACTED = "extracted"
ORIGIN_CONSTRUCTED = "constructed"
ORIGINS = (ORIGIN_EXTRACTED, ORIGIN_CONSTRUCTED)

_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
_BASE32 = re.compile(r"^[A-Z2-7a-z]{32}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class MagnetError(AppError):
    """A magnet URI or infohash could not be understood."""


def _is_hex40(value: str) -> bool:
    return bool(_HEX40.match(value))


def _is_base32(value: str) -> bool:
    return bool(_BASE32.match(value))


def normalize_infohash(value: Any) -> str:
    """Return a lowercase 40-character hex infohash, or ``""`` if invalid.

    Accepts the two spellings used in the wild: 40 hex characters (v1 BTIH) and
    32 base32 characters.  Anything else - including a 32-character *hex* string,
    which is not an infohash - yields ``""``.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or _CONTROL.search(text):
        return ""
    if _is_hex40(text):
        return text.lower()
    if _is_base32(text):
        try:
            raw = base64.b32decode(text.upper(), casefold=True)
        except (binascii.Error, ValueError):
            return ""
        if len(raw) == 20:
            return raw.hex()
    return ""


def is_infohash(value: Any) -> bool:
    return bool(normalize_infohash(value))


@dataclass(frozen=True)
class MagnetLink:
    """One validated magnet URI plus how it was obtained."""

    uri: str
    infohash: str
    display_name: str = ""
    trackers: tuple[str, ...] = ()
    #: ``ORIGIN_EXTRACTED`` (parsed from a URI) or ``ORIGIN_CONSTRUCTED``.
    origin: str = ORIGIN_EXTRACTED

    @property
    def constructed(self) -> bool:
        return self.origin == ORIGIN_CONSTRUCTED

    @property
    def short_infohash(self) -> str:
        return self.infohash[:12] + "…" if len(self.infohash) > 12 else self.infohash

    def describe(self) -> str:
        """One line safe to show in the UI."""
        origin = "built from infohash" if self.constructed else "parsed from link"
        return f"{self.display_name or 'unnamed'} · {origin} · {self.short_infohash}"


def _accepted_tracker(value: str) -> str:
    """Return a tracker URL we can pass to a torrent client, else ``""``.

    Only ``http(s)`` trackers without credentials are kept; ``udp://`` and
    ``wss://`` trackers are accepted for opening in a client, since those are
    ordinary magnet trackers, but nothing is ever contacted from here.
    """
    text = value.strip()
    if not text or _CONTROL.search(text) or len(text) > 500:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in ("http", "https", "udp", "wss"):
        return ""
    host = parsed.hostname or ""
    if not host or "." not in host:
        return ""
    if parsed.username or parsed.password:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not (1 <= port <= 65535):
        return ""
    return text


def parse_magnet(uri: Any) -> MagnetLink:
    """Parse and validate a magnet URI.

    Raises :class:`MagnetError` with a message meant for the user when the text
    is not a magnet URI, carries no usable ``btih`` infohash, or is malformed.
    """
    if not isinstance(uri, str):
        raise MagnetError("A magnet link must be text.")
    text = uri.strip()
    if not text:
        raise MagnetError("The magnet link is empty.")
    if _CONTROL.search(text):
        raise MagnetError("The magnet link contains control characters.")
    if len(text) > MAX_MAGNET_LENGTH:
        raise MagnetError("The magnet link is unreasonably long.")

    split = urlsplit(text)
    if split.scheme.lower() != "magnet":
        raise MagnetError("Only magnet: links are accepted here.")
    if split.netloc:
        raise MagnetError("A magnet link has no host part.")

    # Query keys are conventionally lower-case but the scheme is not the only
    # thing clients get wrong, so normalize the keys before looking at them.
    query = {
        key.lower(): values
        for key, values in parse_qs(split.query, keep_blank_values=True).items()
    }
    infohash = ""
    for candidate in query.get("xt", []):
        value = candidate.strip()
        if value.lower().startswith("urn:btih:"):
            infohash = normalize_infohash(value[len("urn:btih:"):])
            if infohash:
                break
            raise MagnetError("The magnet link has a malformed infohash.")
    if not infohash:
        raise MagnetError("The magnet link has no usable urn:btih infohash.")

    names = [item.strip() for item in query.get("dn", []) if item.strip()]
    display_name = names[0][:MAX_NAME_LENGTH] if names else ""

    trackers: list[str] = []
    for item in query.get("tr", []):
        tracker = _accepted_tracker(item)
        if tracker and tracker not in trackers:
            trackers.append(tracker)
        if len(trackers) >= MAX_TRACKERS:
            break

    return MagnetLink(
        uri=text,
        infohash=infohash,
        display_name=display_name,
        trackers=tuple(trackers),
        origin=ORIGIN_EXTRACTED,
    )


def build_magnet(
    infohash: Any,
    *,
    display_name: str = "",
    trackers: Iterable[str] = (),
) -> MagnetLink:
    """Build a standard magnet URI *from a valid infohash*.

    The result is marked ``constructed``: it records only what the caller knew.
    An invalid infohash raises :class:`MagnetError` - this function never guesses.
    """
    normalized = normalize_infohash(infohash)
    if not normalized:
        raise MagnetError(
            "That is not a valid infohash (40 hex or 32 base32 characters)."
        )

    parts = ["xt=urn:btih:" + normalized]
    raw_name = _CONTROL.sub(" ", str(display_name or ""))
    name = " ".join(raw_name.split())[:MAX_NAME_LENGTH]
    if name:
        parts.append("dn=" + quote(name, safe=""))

    kept: list[str] = []
    for item in trackers or ():
        tracker = _accepted_tracker(str(item))
        if tracker and tracker not in kept:
            kept.append(tracker)
        if len(kept) >= MAX_TRACKERS:
            break
    for tracker in kept:
        parts.append("tr=" + quote(tracker, safe=""))

    return MagnetLink(
        uri="magnet:?" + "&".join(parts),
        infohash=normalized,
        display_name=name,
        trackers=tuple(kept),
        origin=ORIGIN_CONSTRUCTED,
    )


def magnet_from_entry(
    magnet_uri: Any = "",
    infohash: Any = "",
    origin: Any = "",
) -> MagnetLink | None:
    """Best-effort link for stored entry fields; ``None`` when there is none.

    A stored magnet URI wins over a bare infohash, and a bare infohash is only
    ever turned into a URI when it validates.  Malformed stored values yield
    ``None`` instead of an exception, because a single bad field must not stop a
    library entry from loading.
    """
    link: MagnetLink | None = None
    if isinstance(magnet_uri, str) and magnet_uri.strip():
        try:
            link = parse_magnet(magnet_uri)
        except MagnetError:
            link = None
    if link is None and normalize_infohash(infohash):
        try:
            link = build_magnet(infohash)
        except MagnetError:  # pragma: no cover - guarded by normalize_infohash
            return None
    if link is None:
        return None
    if origin == ORIGIN_CONSTRUCTED and link.origin != ORIGIN_CONSTRUCTED:
        # The stored link was built here from a bare infohash; keep saying so.
        return replace(link, origin=ORIGIN_CONSTRUCTED)
    return link


def magnet_fields(link: MagnetLink | None) -> Mapping[str, str]:
    """The library-entry fields for *link* (all empty when there is no link)."""
    if link is None:
        return {"magnet": "", "infohash": "", "magnet_origin": ""}
    return {
        "magnet": link.uri,
        "infohash": link.infohash,
        "magnet_origin": link.origin,
    }


__all__ = [
    "MagnetError",
    "MagnetLink",
    "MAX_MAGNET_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_TRACKERS",
    "ORIGIN_CONSTRUCTED",
    "ORIGIN_EXTRACTED",
    "ORIGINS",
    "build_magnet",
    "is_infohash",
    "magnet_fields",
    "magnet_from_entry",
    "normalize_infohash",
    "parse_magnet",
]
