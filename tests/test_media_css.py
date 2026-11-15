"""The Media page stylesheet: tokens only, readable pairs in both themes.

These checks are static on purpose: the suite runs without a display, so the
CSS is inspected rather than rendered.
"""

from __future__ import annotations

import ast
import py_compile
import re
import tempfile
from pathlib import Path

import pytest

from lgl import theme

CSS_PATH = Path(__file__).resolve().parents[1] / "lgl" / "style.css"

#: The only selectors allowed to carry literal colours: white text sits on a
#: photograph (hero) or a video (player), so those stay dark in both themes.
DARK_OVERLAY_SELECTORS = (
    ".movie-hero",
    ".movie-hero-scrim",
    ".movie-hero-badge",
    ".movie-hero-title",
    ".movie-hero-sub",
    ".movie-hero-meta",
    ".movie-hero-overview",
    ".movie-hero-rating",
    ".movie-hero-badge:backdrop",
    ".movie-hero-title:backdrop",
    ".movie-hero-sub:backdrop",
    ".movie-hero-meta:backdrop",
    ".movie-hero-overview:backdrop",
    ".movie-hero-rating:backdrop",
    ".movie-hero-actions button.movie-btn-dark",
    ".movie-hero-actions button.movie-btn-dark:backdrop",
    ".movie-hero-actions button.movie-btn-dark:hover",
    ".movie-hero-actions button.movie-btn-dark:active",
    ".movie-hero-actions button.movie-btn-dark:checked",
    ".movie-hero-actions button.movie-btn-dark:focus",
    ".movie-hero-actions button.movie-btn-primary",
    ".movie-hero-actions button.movie-btn-primary:backdrop",
    ".movie-hero-actions button.movie-btn-primary:hover",
    ".movie-hero-actions spinbutton",
    ".movie-hero-actions spinbutton:backdrop",
    ".movie-hero-actions spinbutton entry",
    ".movie-hero-actions spinbutton entry:backdrop",
    ".movie-hero-actions spinbutton button",
    ".movie-hero-actions spinbutton button:backdrop",
    ".movie-hero-actions combobox button",
    ".movie-hero-actions combobox button:backdrop",
    ".movie-hero-actions menubutton button",
    ".movie-hero-actions menubutton button:backdrop",
    ".movie-hero-actions combobox arrow",
    ".movie-hero-actions combobox arrow:backdrop",
    ".movie-player-frame webkitwebview",
)

#: The two ends of the hero's background gradient (lightest, darkest).
HERO_GRADIENT_STOPS = ("#171b22", "#0b0d10")

#: Labels drawn on top of that gradient; they must read on either end.
HERO_LABEL_SELECTORS = (
    ".movie-hero-badge",
    ".movie-hero-title",
    ".movie-hero-sub",
    ".movie-hero-meta",
    ".movie-hero-overview",
    ".movie-hero-rating",
)

#: (label, selector, text property, background property) pairs that must read.
TEXT_PAIRS = (
    ("media nav item", "button.movie-nav-item", "color", "@surface"),
    ("active media tab", "button.movie-nav-item.active", "color", "@text"),
    ("primary button", "button.movie-btn-primary", "color", "@text"),
    ("primary button hover", "button.movie-btn-primary:hover", "color", "@hover"),
    ("dark button", "button.movie-btn-dark", "color", "alpha(@text, 0.10)"),
    ("section title", ".movie-section-title", "color", "@bg"),
    ("section sub", ".movie-section-sub", "color", "@bg"),
    ("column row title", ".movie-window .feed-column-row-title", "color", "@bg"),
    ("feed title", ".movie-window .feed-title", "color", "@bg"),
    ("feed meta", ".movie-window .feed-card-meta", "color", "@bg"),
    ("day chip", "button.feed-day", "color", "alpha(@text, 0.04)"),
    ("day chip checked", "button.feed-day:checked", "color", "alpha(@text, 0.16)"),
    ("schedule title", ".movie-window .feed-schedule-title", "color", "@bg"),
    ("schedule time", ".movie-window .feed-schedule-time", "color", "@bg"),
    ("card title", ".movie-card-title", "color", "@bg"),
    ("card meta", ".movie-card-meta", "color", "@bg"),
    ("feed error", ".movie-window .feed-error", "color", "@bg"),
)

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _parse(path: Path) -> dict[str, dict[str, str]]:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    rules: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        declarations = {}
        for part in match.group(2).split(";"):
            if ":" in part:
                name, value = part.split(":", 1)
                declarations[name.strip()] = value.strip()
        for selector in (s.strip() for s in match.group(1).split(",")):
            if selector:
                rules.setdefault(selector, {}).update(declarations)
    return rules


RULES = _parse(CSS_PATH)


def _literal_colours(value: str) -> list[str]:
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", value)
    return [item for item in found if item.lower() not in ("transparent", "none")]


def test_media_rules_use_tokens_not_literal_colours():
    offenders = []
    for selector, declarations in RULES.items():
        if selector in DARK_OVERLAY_SELECTORS:
            continue
        for name, value in declarations.items():
            if name in ("background-image", "border-image") and "gradient" in value:
                continue
            for literal in _literal_colours(value):
                offenders.append(f"{selector} {{ {name}: {literal} }}")
    assert offenders == [], "literal colours outside the hero/player selectors: " + "; ".join(
        offenders[:10]
    )


def test_every_token_used_is_published_by_theme_py():
    published = {theme.css_name(token) for token in theme.DARK}
    used = set()
    for declarations in RULES.values():
        for value in declarations.values():
            used.update(re.findall(r"@([a-z_]+)", value))
    missing = sorted(used - published)
    assert missing == [], f"tokens used in style.css but not in theme.py: {missing}"


def _rgb(value: str) -> tuple[float, float, float]:
    body = value.lstrip("#")
    if len(body) == 3:
        body = "".join(char * 2 for char in body)
    return tuple(float(int(body[i : i + 2], 16)) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _token_value(name: str, palette) -> str:
    for token, raw in palette.items():
        if theme.css_name(token) == name:
            return raw
    raise AssertionError(f"unknown token @{name}")


def _colour(value: str, palette) -> tuple[float, float, float]:
    """Resolve ``@token``, ``alpha(@token, n)`` or a literal hex to RGB."""
    text = value.strip()
    alpha = re.match(r"alpha\(@([a-z_]+),\s*([0-9.]+)\)$", text)
    if alpha:
        base = _rgb(palette["bg"])
        top = _rgb(_token_value(alpha.group(1), palette))
        amount = float(alpha.group(2))
        return tuple(base[i] * (1 - amount) + top[i] * amount for i in range(3))
    if text.startswith("@"):
        return _rgb(_token_value(text[1:], palette))
    if HEX.match(text):
        return _rgb(text)
    raise AssertionError(f"unsupported colour {value!r}")


def _luminance(rgb: tuple[float, float, float]) -> float:
    channels = []
    for channel in rgb:
        channels.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first, second) -> float:
    a, b = _luminance(first), _luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("label,selector,text_prop,background", TEXT_PAIRS)
def test_text_pairs_meet_wcag_aa(label, selector, text_prop, background, mode):
    palette = theme.PALETTES[mode]
    declarations = RULES[selector]
    foreground = _colour(declarations[text_prop], palette)
    backdrop = _colour(background, palette)
    ratio = _contrast(foreground, backdrop)
    assert ratio >= theme.CONTRAST_TEXT, (
        f"{label} ({selector}) is {ratio:.2f}:1 in {mode} mode, "
        f"below {theme.CONTRAST_TEXT}"
    )


def test_active_and_checked_media_states_set_a_colour_pair():
    offenders = []
    for selector, declarations in RULES.items():
        if not selector.startswith(("button.movie-", "button.feed-", "headerbar.movie-nav")):
            continue
        if ".active" not in selector and ":checked" not in selector:
            continue
        if "color" not in declarations or "background-color" not in declarations:
            offenders.append(selector)
    assert offenders == [], f"states without a colour pair: {offenders}"


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("selector", HERO_LABEL_SELECTORS)
@pytest.mark.parametrize("stop", HERO_GRADIENT_STOPS)
def test_hero_labels_read_on_the_hero_gradient(selector, stop, mode):
    """The hero is dark in both themes, so its labels use literal light inks."""
    palette = theme.PALETTES[mode]
    foreground = _colour(RULES[selector]["color"], palette)
    ratio = _contrast(foreground, _rgb(stop))
    assert ratio >= theme.CONTRAST_TEXT, (
        f"{selector} on {stop} is {ratio:.2f}:1 in {mode} mode, below "
        f"{theme.CONTRAST_TEXT}"
    )


MOVIES_UI = Path(__file__).resolve().parents[1] / "lgl" / "movies_ui.py"
LGL = Path(__file__).resolve().parents[1] / "lgl"


def test_the_deleted_stylesheet_helpers_are_gone():
    tree = ast.parse(MOVIES_UI.read_text(encoding="utf-8"))
    banned = {"_cinematic_provider", "_CSS_PROVIDER", "_CINEMATIC_CSS"}
    found = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in banned]
    found += [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in banned]
    assert found == [], f"deleted helpers are still referenced: {sorted(set(found))}"


def test_media_window_never_loads_an_undefined_provider():
    """A leftover ``if provider is not None`` crashed the app at start-up."""
    tree = ast.parse(MOVIES_UI.read_text(encoding="utf-8"))
    checked = []
    for klass in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "MoviesWindow"):
        for func in klass.body:
            if not isinstance(func, ast.FunctionDef) or func.name not in ("__init__", "_build_header"):
                continue
            checked.append(func.name)
            loaded = {
                node.id
                for node in ast.walk(func)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            assert "provider" not in loaded, (
                f"MoviesWindow.{func.name} loads a bare name 'provider' that nothing assigns"
            )
    assert checked == ["__init__", "_build_header"]


def test_every_lgl_module_compiles():
    modules = sorted(LGL.glob("*.py"))
    assert len(modules) > 10
    with tempfile.TemporaryDirectory() as folder:
        for module in modules:
            py_compile.compile(
                str(module), cfile=str(Path(folder) / (module.stem + ".pyc")), doraise=True
            )
