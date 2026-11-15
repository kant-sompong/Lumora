"""The tokens in ``style.css`` must be the shared contract's tokens.

``lgl/design_tokens.py`` lives on the shared-logic branch; until that branch is
merged this test skips rather than inventing a second copy of the palette.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "lgl" / "style.css"

CONTRACT_NAMES = (
    "bg",
    "surface",
    "overlay",
    "border",
    "text",
    "text2",
    "muted",
    "accent",
    "accent_hover",
    "accent_soft",
    "success",
    "warning",
    "danger",
)

_DEFINE = re.compile(r"@define-color\s+([a-z0-9_]+)\s+([^;]+);")
_LIGHT_MARKER = "/* LIGHT palette"


def _split(css: str) -> tuple[str, str]:
    """Return the live (dark) declarations and the documented light ones."""
    index = css.find(_LIGHT_MARKER)
    if index < 0:
        return css, ""
    live = css[:index]
    tail = css[index:]
    end = tail.find("*/")
    return live, tail[:end]


def declared(css: str) -> tuple[dict[str, str], dict[str, str]]:
    live, light = _split(css)
    return (
        {name: value for name, value in _DEFINE.findall(live)},
        {name: value for name, value in _DEFINE.findall(light)},
    )


def design_tokens():
    try:
        from lgl import design_tokens
    except ImportError:  # pragma: no cover - depending on branch stacking
        pytest.skip("lgl/design_tokens.py is not on this branch yet")
    return design_tokens


def normalise(value: str) -> str:
    value = value.strip().lower()
    match = re.fullmatch(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\s*\)", value)
    if match:
        red, green, blue, alpha = match.groups()
        return f"#{int(red):02x}{int(green):02x}{int(blue):02x}@{alpha}"
    return value


def soft(hex_colour: str, alpha: str) -> str:
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{r:02x}{g:02x}{b:02x}@{float(alpha):.2f}"


def test_every_contract_token_is_declared_in_both_modes():
    dark, light = declared(CSS.read_text(encoding="utf-8"))
    assert dark, "style.css declares no tokens"
    assert light, "style.css documents no light values"
    missing = [name for name in CONTRACT_NAMES if name not in dark]
    assert missing == [], f"missing dark tokens: {missing}"
    missing = [name for name in CONTRACT_NAMES if name not in light]
    assert missing == [], f"missing light tokens: {missing}"


def test_the_dark_values_match_the_shared_tokens():
    dark, _light = declared(CSS.read_text(encoding="utf-8"))
    tokens = design_tokens()
    expected = tokens.TOKENS["dark"]
    mismatched = {}
    for name in CONTRACT_NAMES:
        if name == "accent_soft":
            wanted = soft(expected["accent"], str(tokens.TOKENS["dark"].get("accent_soft_alpha", 0.18)))
        else:
            wanted = expected[name]
        if normalise(dark[name]) != normalise(wanted):
            mismatched[name] = (dark[name], wanted)
    assert mismatched == {}


def test_the_light_values_match_the_shared_tokens():
    _dark, light = declared(CSS.read_text(encoding="utf-8"))
    tokens = design_tokens()
    expected = tokens.TOKENS["light"]
    mismatched = {}
    for name in CONTRACT_NAMES:
        if name == "accent_soft":
            wanted = soft(expected["accent"], str(tokens.TOKENS["light"].get("accent_soft_alpha", 0.18)))
        else:
            wanted = expected[name]
        if normalise(light[name]) != normalise(wanted):
            mismatched[name] = (light[name], wanted)
    assert mismatched == {}


def test_rules_name_tokens_instead_of_literal_colours():
    live, _light = _split(CSS.read_text(encoding="utf-8"))
    # Token definitions are where literals belong; every other line must name one.
    rules = _DEFINE.sub("", live)
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", rules)
    assert literals == [], f"literal colours outside the token block: {literals[:10]}"
