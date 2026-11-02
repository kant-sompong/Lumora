"""Cross-platform user configuration.

Linux: ``$XDG_CONFIG_HOME/lumora/config.json``
Windows: ``%APPDATA%\\Lumora\\config.json``

The file is deliberately small and human-readable. Only preferences are stored
here; download queue state lives in the per-user data directory.
"""

from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import Any, Protocol

from . import platform_support
from .util import atomic_write_json, read_json

APP_DIR = "lumora"
#: 4 added the provider API-key map.  Older documents load unchanged.
CONFIG_VERSION = 4
DEFAULT_WINDOW_SIZE = (1100, 720)
MIN_WINDOW_SIZE = (900, 600)

#: Sort orders offered for the local library.
#: Sort orders offered for the archive list (Browse view).
ARCHIVE_SORT_KEYS = (
    "title", "year", "size", "downloads", "downloadable", "creator", "genre",
)
#: Sort orders offered for the local library.
LIBRARY_SORTS = (
    "title", "recent", "size", "year", "added", "played",
    "developer", "genre", "platform",
)
#: Bounds for the download options; values outside are clamped, never stored raw.
MIN_DOWNLOAD_RETRIES = 0
MAX_DOWNLOAD_RETRIES = 20
DEFAULT_DOWNLOAD_RETRIES = 3
MIN_NETWORK_TIMEOUT = 5
MAX_NETWORK_TIMEOUT = 300
DEFAULT_NETWORK_TIMEOUT = 30
MAX_TORRENT_CLIENT = 200
MAX_API_KEY = 256
#: Providers whose credentials the settings store is allowed to hold.
API_KEY_PROVIDERS = ("rawg", "tmdb", "mal")
#: Service name used when the OS credential store is available.
KEYRING_SERVICE = "Lumora"
#: Pre-0.6.0 service names: keys are copied from these on first read so a
#: renamed application does not ask the user to type their keys again.
LEGACY_KEYRING_SERVICES = ("linux-game-library", "NativeGameLibrary")


class KeyringLike(Protocol):
    """The slice of the ``keyring`` API this module uses."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@functools.lru_cache(maxsize=1)
def _auto_keyring() -> KeyringLike | None:
    """Return the OS credential store, or ``None`` when it is unusable.

    ``keyring`` is optional: the app must keep working on a plain desktop with
    no Secret Service, and it must never fall back to the blocked ``fail`` or
    ``null`` backends (they would silently pretend to store a password).
    """
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    module = type(backend).__module__ if backend is not None else ""
    if module.endswith("backends.fail") or module.endswith("backends.null"):
        return None
    return keyring  # type: ignore[return-value]


def _clean_api_key(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if len(text) > MAX_API_KEY:
        return ""
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", text):
        return ""
    return text


def config_path() -> Path:
    return platform_support.config_dir() / "config.json"


class Settings:
    """Load and save per-user preferences.

    Unknown keys are preserved when saving, so a newer version of the app does
    not discard settings written by a future/older build.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        keyring: KeyringLike | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else config_path()
        # ``keyring=None`` auto-detects the OS store; tests inject a fake, and
        # passing a falsy non-None value forces the owner-only file fallback.
        self._keyring: KeyringLike | None = (
            _auto_keyring() if keyring is None else (keyring or None)
        )
        raw = read_json(self.path, default={})
        self._data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        self.keyring_backend = (
            "os-keyring" if self._keyring is not None else "config-file"
        )

    # -- generic access -----------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def update(self, **values: Any) -> None:
        self._data.update(values)
        self.save()

    def save(self) -> None:
        self._data["version"] = CONFIG_VERSION
        atomic_write_json(self.path, self._data)
        # mkstemp already creates the file with 0600 on POSIX; keep that
        # guarantee explicit because the document may hold provider keys.
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # -- provider credentials ----------------------------------------------

    def api_key(self, provider: str) -> str:
        """Return a stored key, or an empty string.  Never log the value."""
        if provider not in API_KEY_PROVIDERS:
            return ""
        if self._keyring is not None:
            try:
                stored = self._keyring.get_password(KEYRING_SERVICE, provider)
            except Exception:
                stored = None
            if stored:
                return _clean_api_key(stored)
            stored = self._key_from_legacy_service(provider)
            if stored:
                return _clean_api_key(stored)
        raw = self._data.get("api_keys")
        if not isinstance(raw, dict):
            return ""
        return _clean_api_key(raw.get(provider, ""))

    def _key_from_legacy_service(self, provider: str) -> str:
        """Copy a key saved under a pre-0.6.0 service name, if there is one.

        The old entry is deliberately left in place: a user who rolls back to
        the previous build still finds their key there.
        """
        if self._keyring is None:
            return ""
        for service in LEGACY_KEYRING_SERVICES:
            try:
                stored = self._keyring.get_password(service, provider)
            except Exception:
                continue
            if not stored:
                continue
            cleaned = _clean_api_key(stored)
            if not cleaned:
                continue
            try:
                self._keyring.set_password(KEYRING_SERVICE, provider, cleaned)
            except Exception:
                pass
            return cleaned
        return ""

    def has_api_key(self, provider: str) -> bool:
        return bool(self.api_key(provider))

    @property
    def api_keys(self) -> dict[str, str]:
        """Read-only snapshot for adapter construction; never log this map."""
        return {
            provider: value
            for provider in API_KEY_PROVIDERS
            if (value := self.api_key(provider))
        }

    def set_api_key(self, provider: str, value: Any) -> None:
        """Store a credential in the OS keyring, or in the owner-only config.

        A working keyring is preferred; the plaintext copy is then removed so a
        key never exists in two places.  If the keyring refuses (no session bus,
        locked collection), the owner-only config file keeps the app usable.
        """
        if provider not in API_KEY_PROVIDERS:
            raise ValueError(f"Unknown API provider: {provider}")
        cleaned = _clean_api_key(value)
        stored_in_keyring = False
        if self._keyring is not None:
            try:
                if cleaned:
                    self._keyring.set_password(KEYRING_SERVICE, provider, cleaned)
                else:
                    self._drop_keyring_entry(provider)
                stored_in_keyring = True
            except Exception:
                stored_in_keyring = False
        keys = self._data.get("api_keys")
        if not isinstance(keys, dict):
            keys = {}
        if cleaned and not stored_in_keyring:
            keys[provider] = cleaned
        else:
            keys.pop(provider, None)
        if keys:
            self._data["api_keys"] = keys
        else:
            self._data.pop("api_keys", None)
        self.save()

    def _drop_keyring_entry(self, provider: str) -> None:
        if self._keyring is None:
            return
        for service in (KEYRING_SERVICE, *LEGACY_KEYRING_SERVICES):
            try:
                self._keyring.delete_password(service, provider)
            except Exception:
                # A missing entry is the state we want to reach, not a failure.
                pass

    def clear_api_key(self, provider: str) -> None:
        self.set_api_key(provider, "")

    # -- download directory -------------------------------------------------

    @property
    def download_dir(self) -> str | None:
        value = self._data.get("download_dir")
        return value if isinstance(value, str) and value else None

    def set_download_dir(self, path: str | Path) -> None:
        self._data["download_dir"] = str(Path(path).expanduser())
        self.save()

    # -- window state -------------------------------------------------------

    @property
    def window_size(self) -> tuple[int, int]:
        raw = self._data.get("window_size")
        try:
            width, height = int(raw[0]), int(raw[1])
        except (TypeError, ValueError, IndexError):
            return DEFAULT_WINDOW_SIZE
        if width < MIN_WINDOW_SIZE[0] or height < MIN_WINDOW_SIZE[1]:
            return DEFAULT_WINDOW_SIZE
        return width, height

    def set_window_size(self, width: int, height: int) -> None:
        if (int(width), int(height)) == self.window_size:
            return
        self._data["window_size"] = [max(MIN_WINDOW_SIZE[0], int(width)),
                                     max(MIN_WINDOW_SIZE[1], int(height))]
        self.save()

    @property
    def maximized(self) -> bool:
        return bool(self._data.get("maximized", False))

    def set_maximized(self, value: bool) -> None:
        if bool(value) != self.maximized:
            self._data["maximized"] = bool(value)
            self.save()

    # -- view state ---------------------------------------------------------

    @property
    def active_view(self) -> str:
        value = self._data.get("active_view")
        return value if value in ("archive", "library") else "archive"

    def set_active_view(self, view: str) -> None:
        if view in ("archive", "library") and view != self.active_view:
            self._data["active_view"] = view
            self.save()

    @property
    def last_game(self) -> str | None:
        value = self._data.get("last_game")
        return value if isinstance(value, str) and value else None

    def set_last_game(self, identifier: str | None) -> None:
        if identifier != self.last_game:
            self._data["last_game"] = identifier or ""
            self.save()

    @property
    def library_selected(self) -> str | None:
        """Identifier of the library entry selected last time."""
        value = self._data.get("library_selected")
        return value if isinstance(value, str) and value else None

    def set_library_selected(self, identifier: str | None) -> None:
        if identifier != self.library_selected:
            self._data["library_selected"] = identifier or ""
            self.save()

    @property
    def library_sort(self) -> str:
        value = self._data.get("library_sort")
        return value if value in LIBRARY_SORTS else "title"

    def set_library_sort(self, value: str) -> None:
        if value in LIBRARY_SORTS and value != self.library_sort:
            self._data["library_sort"] = value
            self.save()

    @property
    def library_sort_descending(self) -> bool | None:
        """Saved direction, or ``None`` to use the mode's natural default.

        ``None`` is meaningful: an older config has no direction at all, and a
        new sort mode starts descending or ascending on its own merits rather
        than inheriting whatever the previous mode was set to.
        """
        value = self._data.get("library_sort_descending")
        return value if isinstance(value, bool) else None

    def set_library_sort_descending(self, value: bool) -> None:
        if bool(value) != self.library_sort_descending:
            self._data["library_sort_descending"] = bool(value)
            self.save()

    @property
    def library_view(self) -> str:
        """``grid`` (cover tiles) or ``list`` (compact rows)."""
        value = self._data.get("library_view")
        return value if value in ("grid", "list") else "grid"

    def set_library_view(self, value: str) -> None:
        if value in ("grid", "list") and value != self.library_view:
            self._data["library_view"] = value
            self.save()

    # -- archive browsing ---------------------------------------------------

    @property
    def browse_sort(self) -> str:
        """Sort field for the archive list; see :data:`ARCHIVE_SORT_KEYS`."""
        value = self._data.get("browse_sort")
        if value == "subject":  # a previous build wrote the old name
            return "genre"
        return value if value in ARCHIVE_SORT_KEYS else "title"

    def set_browse_sort(self, value: str) -> None:
        allowed = ARCHIVE_SORT_KEYS
        if value in allowed and value != self.browse_sort:
            self._data["browse_sort"] = value
            self.save()

    @property
    def browse_descending(self) -> bool:
        return bool(self._data.get("browse_descending", False))

    def set_browse_descending(self, value: bool) -> None:
        if bool(value) != self.browse_descending:
            self._data["browse_descending"] = bool(value)
            self.save()

    # -- appearance ---------------------------------------------------------

    @property
    def theme(self) -> str:
        """``system`` follows the desktop; ``light``/``dark`` force a palette."""
        value = self._data.get("theme")
        return value if value in ("system", "light", "dark") else "system"

    def set_theme(self, value: str) -> None:
        if value in ("system", "light", "dark") and value != self.theme:
            self._data["theme"] = value
            self.save()

    # -- library filtering --------------------------------------------------

    @property
    def library_games_only(self) -> bool:
        """Hide folders that do not look like games (default on)."""
        return bool(self._data.get("library_games_only", True))

    def set_library_games_only(self, value: bool) -> None:
        if bool(value) != self.library_games_only:
            self._data["library_games_only"] = bool(value)
            self.save()

    @property
    def library_show_missing(self) -> bool:
        """Keep entries whose folder disappeared (default off)."""
        return bool(self._data.get("library_show_missing", False))

    def set_library_show_missing(self, value: bool) -> None:
        if bool(value) != self.library_show_missing:
            self._data["library_show_missing"] = bool(value)
            self.save()

    @property
    def library_group_copies(self) -> bool:
        """Show one tile per title when the same game was downloaded twice."""
        return bool(self._data.get("library_group_copies", True))

    def set_library_group_copies(self, value: bool) -> None:
        if bool(value) != self.library_group_copies:
            self._data["library_group_copies"] = bool(value)
            self.save()

    @property
    def library_hide_adult(self) -> bool:
        """Hide entries whose own metadata carries an adult tag."""
        return bool(self._data.get("library_hide_adult", False))

    def set_library_hide_adult(self, value: bool) -> None:
        if bool(value) != self.library_hide_adult:
            self._data["library_hide_adult"] = bool(value)
            self.save()

    # -- downloads and network ---------------------------------------------

    @property
    def download_retries(self) -> int:
        """How often a failed task may be manually retried (0..20)."""
        raw = self._data.get("download_retries")
        if isinstance(raw, bool) or not isinstance(raw, int):
            return DEFAULT_DOWNLOAD_RETRIES
        return max(MIN_DOWNLOAD_RETRIES, min(MAX_DOWNLOAD_RETRIES, raw))

    def set_download_retries(self, value: int) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        number = max(MIN_DOWNLOAD_RETRIES, min(MAX_DOWNLOAD_RETRIES, number))
        if number != self.download_retries:
            self._data["download_retries"] = number
            self.save()

    @property
    def network_timeout(self) -> int:
        """Seconds to wait for a connection before giving up (5..300)."""
        raw = self._data.get("network_timeout")
        if isinstance(raw, bool) or not isinstance(raw, int):
            return DEFAULT_NETWORK_TIMEOUT
        return max(MIN_NETWORK_TIMEOUT, min(MAX_NETWORK_TIMEOUT, raw))

    def set_network_timeout(self, value: int) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        number = max(MIN_NETWORK_TIMEOUT, min(MAX_NETWORK_TIMEOUT, number))
        if number != self.network_timeout:
            self._data["network_timeout"] = number
            self.save()

    @property
    def torrent_client(self) -> str:
        """Optional executable used to open magnet links (empty = system handler)."""
        value = self._data.get("torrent_client")
        return value if isinstance(value, str) and value.strip() else ""

    def set_torrent_client(self, value: str | None) -> None:
        """Store a single executable name/path; arguments and shell syntax are refused."""
        text = (value or "").strip()
        if not text:
            if self._data.pop("torrent_client", None) is not None:
                self.save()
            return
        if len(text) > MAX_TORRENT_CLIENT:
            raise ValueError("The torrent client path is too long.")
        if any(char.isspace() for char in text) or any(
            ord(char) < 32 or ord(char) == 127 for char in text
        ):
            raise ValueError(
                "Enter a single executable path with no arguments or spaces."
            )
        if text != self.torrent_client:
            self._data["torrent_client"] = text
            self.save()
