"""Provider registry and credential checks.

The roadmap asks for one organised provider area that separates metadata
providers from playback providers, says which ones need credentials, reports
connection status without leaking secrets, and can be switched off.

This module owns that truth so the GTK and Qt settings screens only render it.
It never returns a credential: :func:`masked` reports the shape of a stored key,
not its value, and every message is built from lengths and formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

METADATA = "metadata"
PLAYBACK = "playback"
KINDS = (METADATA, PLAYBACK)

#: Settings key holding ``{provider: bool}``.  Missing means enabled.
ENABLED_KEY = "provider_enabled"

READY = "ready"
MISSING_KEY = "missing-key"
INVALID_KEY = "invalid-key"
DISABLED = "disabled"
NOT_CONFIGURED = "not-configured"

_HEX32 = re.compile(r"\A[0-9a-f]{32}\Z", re.I)


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    kind: str
    summary: str
    requires_key: bool = False
    key_setting: str | None = None
    limitation: str = ""
    pattern: re.Pattern[str] | None = None

    @property
    def needs_credentials(self) -> bool:
        return self.requires_key


@dataclass(frozen=True)
class ProviderStatus:
    key: str
    label: str
    kind: str
    summary: str
    state: str
    enabled: bool
    requires_key: bool
    detail: str
    limitation: str = ""

    @property
    def ready(self) -> bool:
        return self.state == READY


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="tmdb",
        label="TMDB",
        kind=METADATA,
        summary="Movie and TV metadata, posters and episode details.",
        requires_key=True,
        key_setting="tmdb",
        pattern=_HEX32,
        limitation="Needs a free TMDB API key; without one the media pages stay empty.",
    ),
    Provider(
        key="mal",
        label="MyAnimeList",
        kind=METADATA,
        summary="Anime titles, airing dates and episode counts.",
        requires_key=True,
        key_setting="mal",
        pattern=_HEX32,
        limitation="Needs a MyAnimeList client ID; release dates can change at short notice.",
    ),
    Provider(
        key="anipm",
        label="Anime schedule",
        kind=METADATA,
        summary="Weekly airing schedule used by the release calendar.",
        limitation="Community data; delayed episodes can be missing or late.",
    ),
    Provider(
        key="rawg",
        label="RAWG",
        kind=METADATA,
        summary="Game catalogue metadata, artwork and release dates.",
        requires_key=True,
        key_setting="rawg",
        pattern=_HEX32,
        limitation="Needs a free RAWG API key; requests are rate limited on that tier.",
    ),
    Provider(
        key="catalog",
        label="Built-in catalogue",
        kind=METADATA,
        summary="The offline game list shipped with the app.",
        limitation="Only knows titles that shipped with this version.",
    ),
    Provider(
        key="direct",
        label="Direct download",
        kind=PLAYBACK,
        summary="Downloads from a URL you paste yourself.",
        limitation="Only plain HTTP(S) links; no accounts, no DRM.",
    ),
    Provider(
        key="torrent",
        label="BitTorrent",
        kind=PLAYBACK,
        summary="Magnet links through the bundled engine.",
        limitation="Needs peers to be reachable; a blocked port looks like a stalled download.",
    ),
    Provider(
        key="framextv",
        label="FrameTV",
        kind=PLAYBACK,
        summary="Streaming source for movies and episodes.",
        limitation="Third-party service; availability and formats are outside our control.",
    ),
    Provider(
        key="kryoto",
        label="Kryoto",
        kind=PLAYBACK,
        summary="Streaming source for series and anime.",
        limitation="Third-party service; some titles are region locked.",
    ),
    Provider(
        key="streaming",
        label="Local player",
        kind=PLAYBACK,
        summary="Opens downloaded files in the system player.",
        limitation="Codec support comes from the operating system, not from Lumora.",
    ),
)

_BY_KEY = {p.key: p for p in PROVIDERS}


def registry() -> tuple[Provider, ...]:
    return PROVIDERS


def get(key: str) -> Provider | None:
    return _BY_KEY.get(str(key or "").strip().lower())


def by_kind(kind: str) -> tuple[Provider, ...]:
    kind = str(kind or "").strip().lower()
    return tuple(p for p in PROVIDERS if p.kind == kind)


def _flags(settings: Any) -> dict[str, bool]:
    raw = settings.get(ENABLED_KEY) if settings is not None else None
    if not isinstance(raw, dict):
        return {}
    return {str(k): bool(v) for k, v in raw.items()}


def is_enabled(settings: Any, key: str) -> bool:
    provider = get(key)
    if provider is None:
        return False
    return _flags(settings).get(provider.key, True)


def set_enabled(settings: Any, key: str, enabled: bool) -> bool:
    provider = get(key)
    if provider is None:
        return False
    flags = _flags(settings)
    flags[provider.key] = bool(enabled)
    settings.set(ENABLED_KEY, flags)
    return True


def stored_credential(settings: Any, provider: Provider) -> str:
    if not provider.key_setting:
        return ""
    keys = settings.get("api_keys") if settings is not None else None
    if not isinstance(keys, dict):
        return ""
    value = keys.get(provider.key_setting)
    return value.strip() if isinstance(value, str) else ""


def validate_credential(provider: Provider | str, value: Any) -> tuple[bool, str]:
    """Check a credential's *shape*.  Messages never repeat the value."""
    provider = get(provider) if isinstance(provider, str) else provider
    if provider is None:
        return False, "Unknown provider."
    if not provider.requires_key:
        return True, "No credentials needed."
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return False, f"{provider.label} needs a key."
    if any(ch.isspace() for ch in text):
        return False, "Keys cannot contain spaces."
    if len(text) < 16:
        return False, "That key looks too short — check you copied all of it."
    if len(text) > 256:
        return False, "That key looks too long — check you copied the right value."
    if provider.pattern is not None and not provider.pattern.match(text):
        return False, "That does not look like the key format this provider issues."
    return True, "Key format looks right. It is only verified once a request succeeds."


def masked(value: Any) -> str:
    """Describe a stored credential without revealing it."""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "not set"
    return f"set ({len(text)} characters)"


def status(settings: Any, *, kind: str | None = None) -> list[ProviderStatus]:
    out: list[ProviderStatus] = []
    for provider in PROVIDERS:
        if kind is not None and provider.kind != kind:
            continue
        enabled = is_enabled(settings, provider.key)
        if not enabled:
            state, detail = DISABLED, "Turned off in settings."
        elif not provider.requires_key:
            state, detail = READY, "Works without an account."
        else:
            key = stored_credential(settings, provider)
            if not key:
                state, detail = MISSING_KEY, f"Add a {provider.label} key to use this."
            else:
                ok, message = validate_credential(provider, key)
                state = READY if ok else INVALID_KEY
                detail = message
        out.append(
            ProviderStatus(
                key=provider.key,
                label=provider.label,
                kind=provider.kind,
                summary=provider.summary,
                state=state,
                enabled=enabled,
                requires_key=provider.requires_key,
                detail=detail,
                limitation=provider.limitation,
            )
        )
    return out


def ready_keys(settings: Any, kind: str | None = None) -> list[str]:
    return [s.key for s in status(settings, kind=kind) if s.ready]


def blocked(settings: Any, kind: str | None = None) -> list[ProviderStatus]:
    return [s for s in status(settings, kind=kind) if not s.ready]


def explain(settings: Any, key: str) -> str:
    """One actionable sentence for a provider, or an empty string."""
    for item in status(settings):
        if item.key == key:
            return "" if item.ready else f"{item.label}: {item.detail}"
    return ""


def credential_providers() -> tuple[Provider, ...]:
    return tuple(p for p in PROVIDERS if p.requires_key)


def summary_lines(settings: Any) -> list[str]:
    """Short, secret-free lines for the diagnostics report."""
    lines = []
    for item in status(settings):
        lines.append(f"{item.label} ({item.kind}): {item.state}")
    return lines


__all__ = [
    "DISABLED",
    "ENABLED_KEY",
    "INVALID_KEY",
    "KINDS",
    "METADATA",
    "MISSING_KEY",
    "NOT_CONFIGURED",
    "PLAYBACK",
    "PROVIDERS",
    "Provider",
    "ProviderStatus",
    "READY",
    "blocked",
    "by_kind",
    "credential_providers",
    "explain",
    "get",
    "is_enabled",
    "masked",
    "ready_keys",
    "registry",
    "set_enabled",
    "status",
    "stored_credential",
    "summary_lines",
    "validate_credential",
]
