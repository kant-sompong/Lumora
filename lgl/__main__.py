"""Entry point: ``python3 -m lgl``.

Windows runs the Tkinter frontend (``lgl.windows_app``); Linux keeps the GTK 3
frontend.  If GTK is unavailable on a non-Windows system the Tkinter frontend
is used as a fallback, so the application still starts on a machine without
PyGObject.
"""

from __future__ import annotations

import sys


def main() -> int:
    if sys.platform == "win32":
        try:
            from .qt_main import main as app_main
        except ImportError:
            # PySide6 missing: keep the legacy Tkinter frontend working.
            from .windows_app import main as app_main

        return int(app_main() or 0)

    try:
        from .ui import main as app_main
    except ImportError:
        from .windows_app import main as app_main

    return int(app_main() or 0)


if __name__ == "__main__":
    sys.exit(main())
