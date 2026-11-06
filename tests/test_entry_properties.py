"""Properties of LibraryEntry must be read, not called.

``display_subtitle`` is a property.  Calling it (``entry.display_subtitle()``)
raised ``TypeError: 'str' object is not callable`` the moment a game was
selected in the Library, in both frontends.
"""

import ast
from pathlib import Path

from lgl.library_store import LibraryEntry

ROOT = Path(__file__).resolve().parents[1] / "lgl"


def test_display_subtitle_is_a_property():
    assert isinstance(LibraryEntry.__dict__["display_subtitle"], property)


def test_no_frontend_calls_display_subtitle():
    offenders = []
    for path in sorted(ROOT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "display_subtitle"
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
