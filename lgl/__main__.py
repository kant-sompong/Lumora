"""Entry point: ``python3 -m lgl``.

The preferred frontend is Qt on Windows and GTK 3 everywhere else; the Tkinter
frontend (``lgl.windows_app``) is only a fallback for a machine where the
preferred one cannot be imported.  Starting the app at all still beats a
traceback, but the reason is logged and shown, so a broken install does not
look like "the app is ugly" instead of "Qt failed to start".
"""

from __future__ import annotations

import sys
import time


def _explain(message: str) -> None:
    """Record why the preferred frontend did not start, and say so."""
    stream = getattr(sys, "stderr", None)
    if stream is not None:
        try:
            print(message, file=stream, flush=True)
        except (OSError, ValueError):
            pass
    try:
        from . import platform_support

        path = platform_support.cache_dir() / "startup.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:  # noqa: BLE001 - a missing log is not worth a crash
        pass
    if stream is None:
        # A windowed build has no console at all, so a dialog is the only way
        # the line can be seen.
        _show_notice(message)


def _show_notice(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning("Lumora", message)
        root.destroy()
    except Exception:  # noqa: BLE001 - no Tk, no dialog; the log remains
        pass


def main() -> int:
    wanted = "Qt" if sys.platform == "win32" else "GTK"
    try:
        if sys.platform == "win32":
            from .qt_main import main as app_main
        else:
            from .ui import main as app_main
    except ImportError as exc:
        _explain(f"{wanted} failed to start: {exc}. Using the basic window.")
        try:
            from .windows_app import main as app_main
        except ImportError as fallback_exc:
            _explain(f"The basic window is not available either: {fallback_exc}.")
            return 1

    return int(app_main() or 0)


if __name__ == "__main__":
    sys.exit(main())
