"""Every module the app imports by name has to reach the Windows bundle.

PyInstaller only bundles what its import graph can see, so a module reached
through ``importlib.import_module("…")`` is silently left out of the packaged
app.  v0.6.23 shipped that way: ``lgl.anime_identity`` was missing from the
Windows build, ``qt_anime_routing.available()`` answered False, and the anime
identifier never ran - with no error to show for it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "windows" / "Lumora.spec"
PACKAGE = ROOT / "lgl"


def _hidden_imports() -> list[str]:
    """The names the spec forces into the bundle."""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "Analysis":
            continue
        for keyword in node.keywords:
            if keyword.arg != "hiddenimports":
                continue
            return [
                element.value
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    raise AssertionError("the Windows spec has no hiddenimports list")


def _dynamic_imports() -> dict[str, str]:
    """``lgl`` modules handed to ``importlib.import_module``, and their file."""
    found: dict[str, str] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            by_attribute = (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            )
            by_name = isinstance(func, ast.Name) and func.id == "import_module"
            if not (by_attribute or by_name):
                continue
            argument = node.args[0]
            name = ""
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                name = argument.value
            elif isinstance(argument, ast.Name):
                name = constants.get(argument.id, "")
            if name.startswith("lgl."):
                found.setdefault(name, path.relative_to(ROOT).as_posix())
    return found


def test_every_module_imported_by_name_is_packaged_for_windows():
    hidden = _hidden_imports()
    missing = {
        name: where
        for name, where in _dynamic_imports().items()
        if name not in hidden
    }
    assert missing == {}, (
        "modules imported by name at runtime are absent from the Windows "
        f"bundle's hiddenimports: {missing}"
    )


def test_the_anime_identifier_reaches_the_windows_bundle():
    """The exact regression 0.6.23 shipped: no identity module at all."""
    assert (PACKAGE / "anime_identity.py").is_file()
    assert "lgl.anime_identity" in _hidden_imports()
