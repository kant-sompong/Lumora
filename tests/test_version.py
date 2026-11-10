"""The application version is declared once, and the packaging asks for it.

``lgl.__version__`` is the canonical string.  The release workflow refuses a
tag that disagrees with it, and both packagers query Python instead of
carrying their own copy, so a bump is a one-line change.  A second hardcoded
"0.6.x" in a frontend or in the installer would ship a build whose About box,
tarball name and installer disagree while every other test still passes, so
pin the single-source rule here.
"""

import ast
import re
from pathlib import Path

import lgl

ROOT = Path(__file__).resolve().parents[1]
LGL = ROOT / "lgl"


def test_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", lgl.__version__), lgl.__version__


def test_app_version_is_declared_only_in_the_package_root():
    """Three-part versions are the app's; ``bittorrent.__version__`` is its own."""
    offenders = []
    for path in sorted(LGL.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue
            if not any(isinstance(t, ast.Name) and t.id == "__version__" for t in targets):
                continue
            literal = getattr(value, "value", None)
            if isinstance(literal, str) and re.fullmatch(r"\d+\.\d+\.\d+", literal):
                offenders.append(f"{path.name}:{node.lineno} = {literal!r}")
    assert offenders == []


def test_windows_installer_version_comes_from_the_command_line():
    iss = (ROOT / "packaging" / "windows" / "Lumora.iss").read_text("utf-8")
    assert "AppVersion={#MyAppVersion}" in iss
    # A real number here would silently outrank the /D the workflow passes in.
    assert '#define MyAppVersion "0.0.0"' in iss


def test_linux_tarball_name_comes_from_the_package_version():
    script = (ROOT / "packaging" / "linux" / "build-tarball.sh").read_text("utf-8")
    assert "print(lgl.__version__)" in script


def test_release_workflow_rejects_a_tag_that_disagrees():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert "python -c \"import lgl; print(lgl.__version__)\"" in workflow
    assert "does not match lgl.__version__" in workflow
