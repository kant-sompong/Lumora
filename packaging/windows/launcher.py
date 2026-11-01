"""PyInstaller entry point for the Windows build.

Kept as a tiny script so the frozen executable has an unambiguous entry point
that does not depend on ``python -m``.
"""

from __future__ import annotations

import sys


def main() -> int:
    from lgl.windows_app import main as app_main

    return int(app_main() or 0)


if __name__ == "__main__":
    sys.exit(main())