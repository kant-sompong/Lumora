"""Update checks against this project's GitHub releases.

Nothing is downloaded or installed automatically.  The check reads the latest
published release, compares its tag with the running version, and—when a newer
one exists—hands the user a link to the release page so they can fetch the build
themselves.  A failed check is a failed check: it never claims to be up to date.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
import time
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


CHECK_ENABLED_KEY = "update_check_enabled"
CHECK_LAST_KEY = "update_check_last"
CHECK_INTERVAL_KEY = "update_check_interval_days"
DEFAULT_INTERVAL_DAYS = 7
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 30

_ARTIFACT_HINT = {
    "Windows": "Download Lumora-Setup-x64.exe, run it, and let the installer replace the old copy.",
    "Linux": "Download the tarball, unpack it somewhere permanent, and update the launcher you already use.",
}


def version_line() -> str:
    """What the About pane shows."""
    return f"Lumora {__version__}"


def check_enabled(settings: Any) -> bool:
    """Update checks are opt-in; anything other than a stored True stays off."""
    return settings.get(CHECK_ENABLED_KEY) is True


def set_check_enabled(settings: Any, enabled: bool) -> None:
    settings.set(CHECK_ENABLED_KEY, bool(enabled))


def interval_days(settings: Any) -> int:
    try:
        days = int(settings.get(CHECK_INTERVAL_KEY))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_DAYS
    return max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, days))


def set_interval_days(settings: Any, days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_DAYS
    value = max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, value))
    settings.set(CHECK_INTERVAL_KEY, value)
    return value


def record_check(settings: Any, when: float | None = None) -> None:
    settings.set(CHECK_LAST_KEY, float(time.time() if when is None else when))


def due(settings: Any, now: float | None = None) -> bool:
    """Whether a scheduled check is both allowed and overdue."""
    if not check_enabled(settings):
        return False
    last = settings.get(CHECK_LAST_KEY)
    if not isinstance(last, (int, float)):
        return True
    current = time.time() if now is None else float(now)
    return current - float(last) >= interval_days(settings) * 86400


def verify_artifact(source: Any, expected_sha256: str) -> tuple[bool, str]:
    """Check a downloaded file or bytes against a published SHA-256.

    A missing or malformed digest is a failure rather than a pass: an update we
    cannot verify is an update we do not install.
    """
    expected = str(expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        return False, "No usable SHA-256 was published for this file, so it was not trusted."
    digest = hashlib.sha256()
    try:
        if isinstance(source, (bytes, bytearray)):
            digest.update(bytes(source))
        else:
            with open(source, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 256), b""):
                    digest.update(chunk)
    except OSError as exc:
        return False, f"The file could not be read: {exc.strerror or exc}."
    actual = digest.hexdigest()
    if hmac.compare_digest(actual, expected):
        return True, "The downloaded file matches the published checksum."
    return False, "The downloaded file does not match its published checksum."


def manual_instructions(platform: str | None = None) -> str:
    label = platform or ("Windows" if sys.platform == "win32" else "Linux")
    hint = _ARTIFACT_HINT.get(label, _ARTIFACT_HINT["Linux"])
    return f"{hint} Releases and their notes live at {RELEASES_PAGE}."


def notes_url(version: str) -> str:
    tag = str(version or "").strip()
    if not tag:
        return RELEASES_PAGE
    return f"{RELEASES_PAGE}/tag/{tag if tag.startswith('v') else 'v' + tag}"


__all__ = [
    "RELEASES_PAGE",
    "RELEASES_URL",
    "ReleaseInfo",
    "UpdateCheckError",
    "UpdateStatus",
    "check_for_update",
    "interpret_release",
    "CHECK_ENABLED_KEY",
    "CHECK_INTERVAL_KEY",
    "CHECK_LAST_KEY",
    "DEFAULT_INTERVAL_DAYS",
    "check_enabled",
    "due",
    "interval_days",
    "is_newer",
    "manual_instructions",
    "notes_url",
    "parse_version",
    "record_check",
    "release_from_json",
    "set_check_enabled",
    "set_interval_days",
    "verify_artifact",
    "version_line",
]
