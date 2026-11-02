"""Small, explicit operating-system adapters.

The rest of the application (archive client, downloader, library, integrity,
compatibility) is platform-neutral and works with :class:`pathlib.Path`.

Only this module knows about XDG directories, Windows AppData, opening folders,
desktop notifications, elevation, and detached processes.  Every function
degrades to a clear, safe result when an optional desktop tool is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MACOS

#: Current folder names.  0.6.0 replaced the pre-rename names below.
APP_DIR_WINDOWS = "Lumora"
APP_DIR_XDG = "lumora"
#: Read-only names used before 0.6.0; see :mod:`lgl.migration`.
LEGACY_APP_DIR_WINDOWS = "NativeGameLibrary"
LEGACY_APP_DIR_XDG = "linux-game-library"


def _system(system: str | None) -> str:
    return system if system is not None else sys.platform


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _home(env: Mapping[str, str]) -> Path:
    value = env.get("USERPROFILE") or env.get("HOME")
    return Path(value).expanduser() if value else Path.home()


def _app_dir(
    kind: str,
    windows_name: str,
    xdg_name: str,
    system: str | None,
    env: Mapping[str, str] | None,
) -> Path:
    """Resolve one of the three per-user directories for any platform."""
    system = _system(system)
    env = _env(env)
    if system.startswith("win"):
        if kind == "config":
            base = env.get("APPDATA")
            root = Path(base) if base else _home(env) / "AppData" / "Roaming"
            return root / windows_name
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        root = Path(base) if base else _home(env) / "AppData" / "Local"
        if kind == "data":
            return root / windows_name
        return root / windows_name / "Cache"
    if system == "darwin":
        if kind == "cache":
            return _home(env) / "Library" / "Caches" / windows_name
        return _home(env) / "Library" / "Application Support" / windows_name
    if kind == "config":
        base = env.get("XDG_CONFIG_HOME")
        root = Path(base) if base else _home(env) / ".config"
    elif kind == "cache":
        base = env.get("XDG_CACHE_HOME")
        root = Path(base) if base else _home(env) / ".cache"
    else:
        base = env.get("XDG_DATA_HOME")
        root = Path(base) if base else _home(env) / ".local" / "share"
    return root / xdg_name


def config_dir(system: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Per-user configuration directory.

    Linux: ``$XDG_CONFIG_HOME/lumora`` (default ``~/.config/lumora``)
    Windows: ``%APPDATA%\\Lumora``
    macOS: ``~/Library/Application Support/Lumora``
    """
    return _app_dir("config", APP_DIR_WINDOWS, APP_DIR_XDG, system, env)


def cache_dir(system: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    return _app_dir("cache", APP_DIR_WINDOWS, APP_DIR_XDG, system, env)


def data_dir(system: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Per-user application data (queue state, favorites, exports)."""
    return _app_dir("data", APP_DIR_WINDOWS, APP_DIR_XDG, system, env)


# -- pre-0.6.0 locations, read only ---------------------------------------

def legacy_config_dir(
    system: str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Where 0.5.0 and earlier kept ``config.json``."""
    return _app_dir(
        "config", LEGACY_APP_DIR_WINDOWS, LEGACY_APP_DIR_XDG, system, env
    )


def legacy_cache_dir(
    system: str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Where 0.5.0 and earlier kept cached artwork."""
    return _app_dir(
        "cache", LEGACY_APP_DIR_WINDOWS, LEGACY_APP_DIR_XDG, system, env
    )


def legacy_data_dir(
    system: str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Where 0.5.0 and earlier kept ``library.json`` and the queues."""
    return _app_dir(
        "data", LEGACY_APP_DIR_WINDOWS, LEGACY_APP_DIR_XDG, system, env
    )


def default_download_dir(system: str | None = None, env: Mapping[str, str] | None = None) -> Path:
    system = _system(system)
    env = _env(env)
    home = _home(env)
    downloads = home / "Downloads"
    if downloads.is_dir():
        return downloads
    # A small number of Windows installs redirect Downloads; USERPROFILE is the
    # safe fallback when the standard directory does not exist.
    return home


def ensure_app_dirs() -> None:
    """Create the per-user folders, copying pre-0.6.0 data across once."""
    from . import migration

    try:
        migration.migrate_once()
    except Exception:  # noqa: BLE001 - a broken migration must not block startup
        pass
    for path in (config_dir(), cache_dir(), data_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def is_admin() -> bool:
    """True when running elevated/root. The app refuses to launch games then."""
    if IS_WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def make_executable(path: Path | str) -> None:
    """Add the user execute bit on POSIX. No-op for Windows executables."""
    if IS_WINDOWS:
        return
    p = Path(path)
    mode = p.stat().st_mode
    os.chmod(p, mode | 0o100)


def is_executable(path: Path | str) -> bool:
    if IS_WINDOWS:
        return Path(path).suffix.lower() in (".exe", ".msi", ".bat", ".cmd", ".com")
    return os.access(path, os.X_OK)


def detached_kwargs() -> dict:
    """Popen keyword arguments for a process that should outlive the UI."""
    if IS_WINDOWS:
        flags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


def _run_quiet(command: list[str]) -> bool:
    if not shutil.which(command[0]):
        return False
    try:
        subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **detached_kwargs(),
        )
        return True
    except OSError:
        return False


def open_folder(path: Path | str) -> bool:
    """Open a directory in the user's file manager. Returns False on failure."""
    p = Path(path).expanduser()
    if not p.is_dir():
        return False
    if IS_WINDOWS:
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if IS_MACOS:
        return _run_quiet(["open", str(p)])
    if _run_quiet(["xdg-open", str(p)]):
        return True
    return _run_quiet(["gio", "open", str(p)])


def open_url(url: str) -> bool:
    """Open an http(s) URL with the user's browser. Never runs a shell."""
    if not url.lower().startswith(("https://", "http://")):
        return False
    if IS_WINDOWS:
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if IS_MACOS:
        return _run_quiet(["open", url])
    if _run_quiet(["xdg-open", url]):
        return True
    return _run_quiet(["gio", "open", url])


def open_magnet(uri: str) -> bool:
    """Open a ``magnet:`` URI with the desktop's registered handler.

    Unlike :func:`open_url` this accepts the magnet scheme, and it still never
    goes through a shell.  Returns False when no handler answers.
    """
    if not isinstance(uri, str):
        return False
    text = uri.strip()
    if not text.lower().startswith("magnet:?"):
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return False
    if IS_WINDOWS:
        try:
            os.startfile(text)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    if IS_MACOS:
        return _run_quiet(["open", text])
    if _run_quiet(["xdg-open", text]):
        return True
    return _run_quiet(["gio", "open", text])


def notify(title: str, message: str, *, error: bool = False) -> bool:
    """Send a best-effort desktop notification.

    Linux uses notify-send when installed. Windows and macOS intentionally
    return False; the UI has an in-app notification area and the platform's
    own notification APIs require additional dependencies that are not worth
    adding to a portable build.
    """
    if IS_WINDOWS or IS_MACOS:
        return False
    command = ["notify-send", "--app-name=Lumora"]
    if error:
        command.append("--urgency=critical")
    command.extend([title, message])
    return _run_quiet(command)


def notification_backend() -> str | None:
    if IS_WINDOWS or IS_MACOS:
        return None
    return "notify-send" if shutil.which("notify-send") else None


def free_space(path: Path | str) -> int:
    """Free bytes on the filesystem containing *path*, or 0 if unknown."""
    try:
        usage = shutil.disk_usage(str(path))
        return int(usage.free)
    except OSError:
        return 0


def platform_name() -> str:
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    return "Linux"


def supports_linux_launch() -> bool:
    return IS_LINUX


def can_verify_executable_permissions() -> bool:
    return not IS_WINDOWS
