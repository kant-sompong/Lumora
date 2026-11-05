"""Explicit, non-root launching of reviewed downloaded files.

The GUI must show a confirmation dialog before calling :func:`launch`.
No shell is involved (``shell=False``, argument list), and paths must resolve
inside the chosen library directory.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .filetypes import is_appimage, is_shell, looks_like_elf
from .util import LaunchError, is_within

#: Interpreter used when a script does not name its own.
SHELL_INTERPRETER = "/bin/sh"

#: Suffixes that are never the way into a game, even when marked executable.
_NEVER_SUFFIXES = (
    ".debug", ".pdb", ".sym", ".dll", ".so", ".dylib", ".pak", ".dat",
    ".json", ".ini", ".cfg", ".txt", ".md", ".pdf", ".log",
)

#: Helper programs that ship beside a game and look executable.  These patterns
#: are deliberately specific: a game called "Crash Bandicoot" must survive.
_HELPER_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"crash(pad|handler|reporter)?\d*([_.\-]|$)",
        r"unins",
        r"(^|[_.\-])(setup|installer|install|redist|vcredist|dxsetup|dotnet)([_.\-]|$)",
        r"(easyanticheat|battleye|steamerrorreporter|steam_api)",
        r"(^|[_.\-])(updater|update|patch)([_.\-]|$)",
        r"(^|[_.\-])helper([_.\-]|$)",
    )
)

_WINDOWS_SUFFIXES = (".exe", ".bat", ".cmd")
_NATIVE_SUFFIXES = (".sh", ".appimage", ".run")
_LAUNCH_WORDS = ("start", "run", "launch", "play", "game", "main")


def _on_windows() -> bool:
    """True on the Windows build, where a .exe starts directly."""
    return os.name == "nt"


def _is_root() -> bool:
    """POSIX root check; simply False where ``geteuid`` does not exist."""
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid is not None and geteuid() == 0)


def _compact(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _title_match(stem: str, title: str) -> bool:
    """Does this file name look named after the game?

    Only the title is allowed to be contained in the file name, never the other
    way round: otherwise a file called ``game`` would "match" every title that
    happens to contain that word.
    """
    compact_title = _compact(title)
    compact_stem = _compact(stem)
    if len(compact_title) < 4 or len(compact_stem) < 4:
        return False
    if compact_title in compact_stem:
        return True
    # A long word from the title is a good sign even when the subtitle is used:
    # "Hollow Knight: Silksong" still matches a file called ``Silksong``.
    words = [
        word for word in re.split(r"[^0-9a-z]+", title.casefold()) if len(word) >= 5
    ]
    return any(word in stem.casefold() for word in words)


def launcher_rank(name: str, *, kind: str = "", title: str = "") -> int | None:
    """How plausible is *name* as the thing you start?  ``None`` means "no".

    Lower ranks are offered first.  Only the choices a person recognises are
    kept: shell scripts, Windows executables (offered for Wine/Proton),
    AppImages, and native executables whose name looks like a launcher instead
    of a debug build, a crash handler or an installer.
    """
    lowered = name.casefold().strip()
    if not lowered or lowered.startswith("."):
        return None
    if lowered.endswith(_NEVER_SUFFIXES):
        return None
    stem = Path(lowered).stem
    for pattern in _HELPER_PATTERNS:
        if pattern.search(stem):
            return None

    named = _title_match(stem, title)
    launch_word = any(word in stem for word in _LAUNCH_WORDS)
    if lowered.endswith(_WINDOWS_SUFFIXES):
        # A Windows build is worth choosing, but it needs Wine/Proton to run;
        # plan_launch uses Wine when it is installed and says so when it is not.
        return 20 if named else 24
    if lowered.endswith(_NATIVE_SUFFIXES):
        return 0 if named else (2 if launch_word else 6)
    if "elf" in (kind or "").casefold():
        return 1 if named else (3 if launch_word else 10)
    return None


@dataclass(frozen=True)
class LaunchPlan:
    path: Path
    kind: str
    command: list[str]
    cwd: Path


def _wine() -> str | None:
    """Path to Wine, or ``None`` when a Windows build cannot start here."""
    if os.name == "nt":
        return None
    return shutil.which("wine") or shutil.which("wine64")


def _windows_command(target: Path) -> list[str]:
    """Start a Windows build: directly on Windows, through Wine on Linux."""
    if _on_windows():
        if target.suffix.casefold() in (".bat", ".cmd"):
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(target)]
        return [str(target)]
    wine = _wine()
    if wine is None:
        raise LaunchError(
            "This is a Windows build. Install Wine (or run it through Proton) to "
            "start it from Lumora. The file was not launched."
        )
    if target.suffix.casefold() in (".bat", ".cmd"):
        return [wine, "cmd", "/c", str(target)]
    return [wine, str(target)]


def _shell_command(target: Path) -> list[str]:
    """Build the command for a shell script, honouring its shebang.

    ``/bin/sh`` is dash on Debian and Ubuntu, where a ``#!/bin/bash`` script
    fails on ``[[``, arrays and most of what bash start scripts use.  So bash
    is used when the first line asks for it (directly or through ``env``), and
    ``/bin/sh`` stays the default for everything else.
    """
    interpreter = SHELL_INTERPRETER
    try:
        with open(target, "rb") as handle:
            first_line = handle.readline(160)
    except OSError:
        return [interpreter, str(target)]
    line = first_line.decode("utf-8", "replace").strip()
    if line.startswith("#!"):
        words = line[2:].strip().split()
        if words and Path(words[0]).name == "env" and len(words) > 1:
            words = words[1:]
        if words and Path(words[0]).name.startswith("bash"):
            bash = shutil.which("bash")
            if bash is None and Path("/bin/bash").exists():
                bash = "/bin/bash"
            if bash is not None:
                interpreter = bash
    return [interpreter, str(target)]


def _kind_for(path: Path) -> str:
    if path.suffix.casefold() in _WINDOWS_SUFFIXES:
        return "Windows build"
    if is_shell(path.name):
        return "shell installer"
    if is_appimage(path.name):
        return "AppImage"
    if looks_like_elf(path):
        return "ELF executable"
    raise LaunchError(
        "This file is not a shell script, AppImage, or ELF executable. "
        "It was not launched."
    )


def plan_launch(path: Path | str, library_root: Path | str) -> LaunchPlan:
    if _is_root():
        raise LaunchError("Refusing to launch games as root.")
    target = Path(path).expanduser()
    if target.is_symlink():
        target = target.resolve()
    else:
        target = target.resolve()
    root = Path(library_root).expanduser().resolve()
    if not is_within(target, root):
        raise LaunchError("The file is outside the selected download library. It was not launched.")
    if not target.is_file():
        raise LaunchError("The selected file no longer exists.")
    kind = _kind_for(target)
    if kind == "shell installer":
        command = _shell_command(target)
    elif kind == "Windows build":
        command = _windows_command(target)
    else:
        if not os.access(target, os.X_OK):
            raise LaunchError(
                "This executable is not marked executable. Use “Make executable” only if you "
                "have reviewed the file."
            )
        command = [str(target)]
    return LaunchPlan(path=target, kind=kind, command=command, cwd=target.parent)


def make_executable(path: Path | str, library_root: Path | str) -> None:
    """Add the user execute bit after the user explicitly approved the file."""
    if _is_root():
        raise LaunchError("Refusing to modify files as root.")
    target = Path(path).expanduser().resolve()
    root = Path(library_root).expanduser().resolve()
    if not is_within(target, root) or not target.is_file():
        raise LaunchError("Refusing to change permissions outside the download library.")
    if is_shell(target.name):
        raise LaunchError("Shell scripts are run through an interpreter and do not need an execute bit.")
    # Check first: a refused file must be left exactly as it was, because the
    # dialog says the file was not changed.
    appimage = target.name.lower().endswith(".appimage")
    if not appimage and not looks_like_elf(target):
        raise LaunchError("This file is not a recognized native executable.")
    mode = target.stat().st_mode
    os.chmod(target, mode | 0o100)


def open_launch_log(folder: Path | str) -> tuple[TextIO | None, Path | None]:
    """Open a log for a launch from *folder*, or return ``(None, None)``.

    The game's own folder is preferred, but it can be read-only (a mounted
    image, a shared library) and a log is never a reason to refuse a launch:
    a per-game log in the data folder is tried next, and failing that the
    program simply runs without one.
    """
    folder = Path(folder)
    candidates = [folder / "launch.log"]
    try:
        from . import platform_support

        candidates.append(
            platform_support.data_dir() / "launch-logs" / f"{folder.name or 'game'}.log"
        )
    except Exception:  # noqa: BLE001 - the log is optional
        pass
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return open(path, "a", encoding="utf-8"), path
        except OSError:
            continue
    return None, None


def launch(path: Path | str, library_root: Path | str) -> tuple[int, Path | None]:
    """Launch a reviewed file. Returns (pid, log_path).

    Call this only after an explicit user confirmation in the UI.
    """
    plan = plan_launch(path, library_root)
    log_file, log_path = open_launch_log(plan.cwd)
    try:
        if log_file is not None:
            log_file.write(
                f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} launching {plan.path.name} "
                f"({plan.kind}) ===\n"
            )
            log_file.write("command: " + shlex.join(plan.command) + "\n")
            log_file.flush()
        process = subprocess.Popen(
            plan.command,
            cwd=str(plan.cwd),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=log_file if log_file is not None else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise LaunchError(f"Could not start the program: {exc}") from exc
    finally:
        if log_file is not None:
            log_file.close()
    return process.pid, log_path
