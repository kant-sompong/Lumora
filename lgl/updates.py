"""Update checks against this project's GitHub releases.

Nothing is downloaded or installed automatically.  The check reads the latest
published release, compares its tag with the running version, and—when a newer
one exists—hands the user a link to the release page so they can fetch the build
themselves.  A failed check is a failed check: it never claims to be up to date.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from . import __version__

RELEASES_URL = "https://api.github.com/repos/kant-sompong/Lumora/releases/latest"
RELEASES_PAGE = "https://github.com/kant-sompong/Lumora/releases"
USER_AGENT = "Lumora-update-check"
_TIMEOUT = 8.0
_MAX_BYTES = 256 * 1024


class UpdateCheckError(Exception):
    """The latest release could not be read."""


@dataclass(frozen=True)
class ReleaseInfo:
    """The fields of a GitHub release this app actually uses."""

    tag: str
    version: str
    url: str
    notes: str = ""


@dataclass(frozen=True)
class UpdateStatus:
    """Answer to "is there a newer release than the one running?"."""

    current: str
    latest: str
    newer: bool
    url: str
    message: str
    notes: str = ""


def parse_version(text: object) -> tuple[int, ...]:
    """Read numeric parts of a version, ignoring a leading ``v`` and suffixes."""
    raw = str(text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in raw.split(".")[:4]:
        match = re.match(r"\d+", chunk)
        if match is None:
            break
        parts.append(int(match.group()))
        if match.group() != chunk:
            # A suffix (``0-beta``) ends the numeric version: a pre-release is
            # not newer than the release it precedes.
            break
    return tuple(parts)


def is_newer(current: object, latest: object) -> bool:
    """True when *latest* is a later release than *current*.

    Unreadable versions are never treated as an update: claiming "newer" from a
    version nobody could parse would nag the user forever.
    """
    left = parse_version(latest)
    right = parse_version(current)
    if not left or not right:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def release_from_json(data: Any) -> ReleaseInfo:
    """Build :class:`ReleaseInfo` from the API document, or raise."""
    if not isinstance(data, dict):
        raise UpdateCheckError("The release list was not a JSON object.")
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise UpdateCheckError("The latest release has no tag.")
    url = str(data.get("html_url") or "").strip() or RELEASES_PAGE
    notes = str(data.get("body") or "").strip()[:2000]
    return ReleaseInfo(tag=tag, version=tag.lstrip("vV"), url=url, notes=notes)


def interpret_release(data: Any, current: str = __version__) -> UpdateStatus:
    """Compare one API document with the running version."""
    release = release_from_json(data)
    newer = is_newer(current, release.version)
    if newer:
        message = f"Lumora {release.version} is available (you have {current})."
    else:
        message = f"Lumora {current} is the latest release."
    return UpdateStatus(
        current=current,
        latest=release.version,
        newer=newer,
        url=release.url,
        message=message,
        notes=release.notes,
    )


def _fetch_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            if status != 200:
                raise UpdateCheckError(f"The release list answered HTTP {status}.")
            raw = response.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateCheckError("No release has been published yet.") from exc
        raise UpdateCheckError(
            f"The release list answered HTTP {exc.code}."
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateCheckError(
            f"Could not reach GitHub: {getattr(exc, 'reason', exc)}"
        ) from exc
    if len(raw) > _MAX_BYTES:
        raise UpdateCheckError("The release list was unexpectedly large.")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise UpdateCheckError("The release list was not valid JSON.") from exc


def check_for_update(
    current: str = __version__,
    *,
    fetcher: Callable[[str, float], Any] | None = None,
    timeout: float = _TIMEOUT,
) -> UpdateStatus:
    """Read the latest release and compare it with *current*."""
    data = (fetcher or _fetch_json)(RELEASES_URL, timeout)
    return interpret_release(data, current)


__all__ = [
    "RELEASES_PAGE",
    "RELEASES_URL",
    "ReleaseInfo",
    "UpdateCheckError",
    "UpdateStatus",
    "check_for_update",
    "interpret_release",
    "is_newer",
    "parse_version",
    "release_from_json",
]
