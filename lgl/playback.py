"""Playback failure handling, resuming and track selection.

Roadmap section 5 asks for clear failures, retry and recovery actions, kept
progress, subtitle/audio selection where the backend allows it, consistent
behaviour across systems, and honest explanations for formats we cannot play.

The module is deliberately free of any player binding: it decides *what* to say
and *what* to offer, and the frontends decide how to show it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Failures a player can run into, coarse enough to act on.
NETWORK = "network"
OFFLINE = "offline"
TIMEOUT = "timeout"
NOT_FOUND = "not-found"
FORBIDDEN = "forbidden"
AUTH = "auth"
FORMAT = "format"
DRM = "drm"
NO_SPACE = "no-space"
UNKNOWN = "unknown"

KINDS = (NETWORK, OFFLINE, TIMEOUT, NOT_FOUND, FORBIDDEN, AUTH, FORMAT, DRM, NO_SPACE, UNKNOWN)

RETRY = "retry"
CHOOSE_SOURCE = "choose-source"
OPEN_SETTINGS = "settings"
OPEN_FOLDER = "open-folder"
COPY_LINK = "copy-link"
INSTALL_CODEC = "install-codec"
FREE_SPACE = "free-space"

MAX_ATTEMPTS = 2

_MESSAGES = {
    NETWORK: "The stream dropped. Lumora is still running — try again when the connection settles.",
    OFFLINE: "You appear to be offline. Downloaded titles still play.",
    TIMEOUT: "The source took too long to answer.",
    NOT_FOUND: "The source no longer has this title. Pick another source.",
    FORBIDDEN: "The source refused this request. It may be region locked.",
    AUTH: "The provider rejected the stored credentials. Update them in Settings.",
    FORMAT: "This file uses a format the bundled player cannot decode.",
    DRM: "This title is copy protected, so it cannot be played here.",
    NO_SPACE: "There is not enough free disk space to buffer this title.",
    UNKNOWN: "Playback failed for an unknown reason.",
}

_ACTIONS = {
    NETWORK: (RETRY, CHOOSE_SOURCE, COPY_LINK),
    OFFLINE: (RETRY, OPEN_FOLDER),
    TIMEOUT: (RETRY, CHOOSE_SOURCE),
    NOT_FOUND: (CHOOSE_SOURCE, COPY_LINK),
    FORBIDDEN: (CHOOSE_SOURCE, OPEN_SETTINGS),
    AUTH: (OPEN_SETTINGS, CHOOSE_SOURCE),
    FORMAT: (INSTALL_CODEC, OPEN_FOLDER),
    DRM: (CHOOSE_SOURCE,),
    NO_SPACE: (FREE_SPACE, OPEN_FOLDER),
    UNKNOWN: (RETRY, CHOOSE_SOURCE, COPY_LINK),
}

ACTION_LABELS = {
    RETRY: "Try again",
    CHOOSE_SOURCE: "Choose another source",
    OPEN_SETTINGS: "Open settings",
    OPEN_FOLDER: "Show in folder",
    COPY_LINK: "Copy link",
    INSTALL_CODEC: "Open codec help",
    FREE_SPACE: "Free up space",
}

_CODEC_HINT = {
    ".mkv": "MKV containers usually need extra codecs on Windows; the in-app player handles most of them.",
    ".avi": "AVI often carries older codecs; the system player may handle it better.",
    ".mov": "QuickTime files sometimes use codecs only the system player can decode.",
    ".webm": "WebM needs VP8/VP9 decoding, which the bundled player supports but older CPUs handle slowly.",
    ".flv": "Flash-era containers are not supported by the bundled player.",
}


@dataclass(frozen=True)
class Capabilities:
    """What the current playback backend can actually do."""

    audio_tracks: bool = False
    subtitles: bool = False
    external_subtitles: bool = False
    resume: bool = False
    seek: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "Capabilities":
        raw = raw or {}
        return cls(
            audio_tracks=bool(raw.get("audio_tracks", False)),
            subtitles=bool(raw.get("subtitles", False)),
            external_subtitles=bool(raw.get("external_subtitles", False)),
            resume=bool(raw.get("resume", False)),
            seek=bool(raw.get("seek", True)),
        )


@dataclass(frozen=True)
class Track:
    id: str
    label: str = ""
    language: str = ""
    codec: str = ""
    forced: bool = False
    channels: int = 0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Track":
        return cls(
            id=str(raw.get("id") or ""),
            label=str(raw.get("label") or ""),
            language=str(raw.get("language") or raw.get("lang") or ""),
            codec=str(raw.get("codec") or ""),
            forced=bool(raw.get("forced", False)),
            channels=int(raw.get("channels") or 0),
        )


@dataclass
class Selection:
    audio: Track | None = None
    subtitle: Track | None = None
    note: str = ""

    @property
    def has_subtitle(self) -> bool:
        return self.subtitle is not None


@dataclass
class Failure:
    """A classified playback failure with the actions worth offering."""

    kind: str
    message: str
    actions: tuple[str, ...] = ()
    retryable: bool = False
    detail: str = ""


def classify(error: Any) -> str:
    status = getattr(error, "status", None) or getattr(error, "code", None)
    text = str(error or "").lower()
    if isinstance(status, int):
        if status in (401, 407):
            return AUTH
        if status == 403:
            return FORBIDDEN
        if status == 404:
            return NOT_FOUND
        if status == 410:
            return NOT_FOUND
    name = type(error).__name__.lower() if error is not None else ""
    if isinstance(error, TimeoutError) or "timeout" in name or "timed out" in text:
        return TIMEOUT
    if "enoent" in name or "no such file" in text:
        return NOT_FOUND
    if "encod" in text or "codec" in text or "decoder" in text or "unsupported format" in text:
        return FORMAT
    if "drm" in text or "copy protection" in text:
        return DRM
    if "no space" in text or "disk full" in text:
        return NO_SPACE
    if "offline" in text or "name or service not known" in text:
        return OFFLINE
    if "403" in text or "forbidden" in text:
        return FORBIDDEN
    if "401" in text or "unauthorized" in text:
        return AUTH
    if "404" in text or "not found" in text or "gone" in text:
        return NOT_FOUND
    if "refused" in text or "reset by peer" in text or "connection" in text or "network" in text:
        return NETWORK
    return UNKNOWN


def message(kind: str, *, title: str = "", provider: str = "") -> str:
    base = _MESSAGES.get(kind, _MESSAGES[UNKNOWN])
    if title:
        base = f"“{title}” could not play. {base}"
    if provider and kind in (NOT_FOUND, FORBIDDEN, AUTH):
        base = f"{base} (source: {provider})"
    return base


def actions(kind: str) -> tuple[str, ...]:
    return _ACTIONS.get(kind, _ACTIONS[UNKNOWN])


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def should_retry(kind: str, attempt: int, *, max_attempts: int = MAX_ATTEMPTS) -> bool:
    if kind not in (NETWORK, OFFLINE, TIMEOUT, UNKNOWN):
        return False
    try:
        attempt = int(attempt)
    except (TypeError, ValueError):
        return False
    return attempt < max(0, int(max_attempts))


def failure(error: Any, *, title: str = "", provider: str = "", attempt: int = 0) -> Failure:
    kind = classify(error)
    return Failure(
        kind=kind,
        message=message(kind, title=title, provider=provider),
        actions=actions(kind),
        retryable=should_retry(kind, attempt),
        detail=str(error or "")[:300],
    )


def resume_position(
    position: Any,
    duration: Any = None,
    *,
    min_resume: float = 5.0,
    near_end_ratio: float = 0.95,
) -> float:
    """Where to start playback, in seconds; ``0`` means from the beginning."""
    try:
        start = float(position)
    except (TypeError, ValueError):
        return 0.0
    if start <= 0 or start < min_resume:
        return 0.0
    try:
        total = float(duration) if duration is not None else 0.0
    except (TypeError, ValueError):
        total = 0.0
    if total > 0 and start / total >= near_end_ratio:
        return 0.0
    if total > 0 and start >= total:
        return 0.0
    return start


def _match(tracks: Sequence[Track], language: str) -> Track | None:
    wanted = str(language or "").strip().lower()
    if not wanted:
        return None
    for track in tracks:
        code = track.language.lower()
        if code == wanted or code.split("-")[0] == wanted.split("-")[0]:
            return track
    return None


def select_tracks(
    audio: Iterable[Any] = (),
    subtitles: Iterable[Any] = (),
    *,
    language: str = "",
    capabilities: Capabilities | None = None,
    want_subtitles: bool = False,
) -> Selection:
    """Choose one audio and (optionally) one subtitle track.

    Falls back to the first track of each kind, and explains when the backend
    cannot expose tracks at all instead of silently ignoring the request.
    """
    caps = capabilities or Capabilities()
    audio_tracks = [t if isinstance(t, Track) else Track.from_mapping(t) for t in audio]
    subs = [t if isinstance(t, Track) else Track.from_mapping(t) for t in subtitles]
    selection = Selection()

    if audio_tracks:
        if not caps.audio_tracks:
            selection.note = "This player cannot switch audio tracks; it plays the default one."
        else:
            selection.audio = _match(audio_tracks, language) or audio_tracks[0]

    if want_subtitles or subs:
        forced = next((t for t in subs if t.forced), None)
        if not subs:
            selection.note = selection.note or "No subtitles are available for this title."
        elif not (caps.subtitles or caps.external_subtitles):
            selection.note = selection.note or "This player cannot show subtitles."
        else:
            selection.subtitle = (
                _match(subs, language) or forced or (subs[0] if want_subtitles else None)
            )
    return selection


def unsupported_format(name: str) -> str:
    """Explain why an extension may not play, without pretending to know more."""
    text = str(name or "").strip().lower()
    for ext, hint in _CODEC_HINT.items():
        if text.endswith(ext):
            return hint
    suffix = f" ({text})" if text.startswith(".") else ""
    return f"The bundled player cannot decode this format{suffix}. The system player may."


def capability_summary(caps: Capabilities) -> str:
    bits = []
    bits.append("resume" if caps.resume else "no resume")
    bits.append("audio tracks" if caps.audio_tracks else "single audio")
    bits.append("subtitles" if caps.subtitles else "no subtitles")
    return ", ".join(bits)


__all__ = [
    "AUTH",
    "ACTION_LABELS",
    "CHOOSE_SOURCE",
    "COPY_LINK",
    "Capabilities",
    "DRM",
    "Failure",
    "FORBIDDEN",
    "FORMAT",
    "FREE_SPACE",
    "INSTALL_CODEC",
    "KINDS",
    "MAX_ATTEMPTS",
    "NETWORK",
    "NOT_FOUND",
    "NO_SPACE",
    "OFFLINE",
    "OPEN_FOLDER",
    "OPEN_SETTINGS",
    "RETRY",
    "Selection",
    "TIMEOUT",
    "Track",
    "UNKNOWN",
    "action_label",
    "actions",
    "capability_summary",
    "classify",
    "failure",
    "message",
    "resume_position",
    "select_tracks",
    "should_retry",
    "unsupported_format",
]
