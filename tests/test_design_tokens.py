"""The shared design tokens: one palette and one scale for both apps.

The contrast checks are the point: a colour that reads well on bg but not on an
overlay panel is a bug the contract forbids, so it fails here rather than in a
screenshot review.
"""

from __future__ import annotations

import pytest

from lgl import design_tokens
from lgl.design_tokens import (
    ACCENT_SOFT_ALPHA,
    RADIUS,
    SPACING,
    TEXT_SIZES,
    TOKENS,
    accent_soft,
    contrast_ratio,
)

THEMES = ("dark", "light")
COLOURS = (
    "bg", "surface", "overlay", "border", "text", "text2", "muted",
    "accent", "accent_hover", "success", "warning", "danger",
)
TEXT_LEVELS = ("text", "text2", "muted")


def test_every_colour_exists_in_both_themes():
    for theme in THEMES:
        assert theme in TOKENS
        for name in COLOURS:
            value = TOKENS[theme].get(name)
            assert isinstance(value, str), f"{theme}.{name} is missing"
            assert value.startswith("#") and len(value) == 7, f"{theme}.{name}={value!r}"
            int(value[1:], 16)  # parses as hex


def test_the_contract_values_are_kept():
    assert TOKENS["dark"]["bg"] == "#0f1014"
    assert TOKENS["dark"]["surface"] == "#16181d"
    assert TOKENS["dark"]["overlay"] == "#1d2027"
    assert TOKENS["dark"]["border"] == "#2a2d36"
    assert TOKENS["dark"]["text"] == "#e8e9ee"
    assert TOKENS["dark"]["text2"] == "#a4a7b3"
    assert TOKENS["dark"]["accent"] == "#8b5cf6"
    assert TOKENS["dark"]["accent_hover"] == "#9d74f8"
    assert TOKENS["light"]["bg"] == "#f4f5f8"
    assert TOKENS["light"]["surface"] == "#ffffff"
    assert TOKENS["light"]["overlay"] == "#ffffff"
    assert TOKENS["light"]["border"] == "#dcdfe6"
    assert TOKENS["light"]["text"] == "#14161c"
    assert TOKENS["light"]["text2"] == "#4a4f5c"
    assert TOKENS["light"]["accent"] == "#7c3aed"
    assert TOKENS["light"]["accent_hover"] == "#6d28d9"
    assert TOKENS["dark"]["success"] == TOKENS["light"]["success"] == "#22c55e"
    assert TOKENS["dark"]["warning"] == TOKENS["light"]["warning"] == "#f59e0b"
    assert TOKENS["dark"]["danger"] == TOKENS["light"]["danger"] == "#ef4444"


def test_muted_is_the_documented_adjustment():
    """The only contract value that changed, and why, is recorded in the module."""
    assert TOKENS["dark"]["muted"] == "#858998"
    assert TOKENS["light"]["muted"] == "#6b7080"
    assert "4.15" in (design_tokens.__doc__ or "")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("level", TEXT_LEVELS)
@pytest.mark.parametrize("surface", ("bg", "surface", "overlay"))
def test_text_reaches_4_5_to_1_on_every_surface(theme, level, surface):
    ratio = contrast_ratio(TOKENS[theme][level], TOKENS[theme][surface])
    assert ratio >= 4.5, f"{theme}.{level} on {surface}: {ratio:.2f}"


@pytest.mark.parametrize("theme", THEMES)
def test_accent_soft_is_the_accent_at_18_percent(theme):
    assert TOKENS[theme]["accent_soft_alpha"] == ACCENT_SOFT_ALPHA == 0.18
    accent = TOKENS[theme]["accent"].lstrip("#")
    expected = (
        f"rgba({int(accent[0:2], 16)}, {int(accent[2:4], 16)}, "
        f"{int(accent[4:6], 16)}, 0.18)"
    )
    assert accent_soft(theme) == expected


def test_the_scales_match_the_contract():
    assert SPACING == (4, 8, 12, 16, 24, 32)
    assert RADIUS == (6, 10, 14)
    assert TEXT_SIZES == {
        "caption": 11, "body": 13, "label": 14, "title": 18, "header": 22,
    }


def test_contrast_ratio_is_the_wcag_definition():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)
    # Symmetric, so callers cannot get a different answer by swapping.
    assert contrast_ratio("#8b5cf6", "#0f1014") == pytest.approx(
        contrast_ratio("#0f1014", "#8b5cf6")
    )
    assert contrast_ratio("#858998", "#1d2027") >= 4.5


def test_an_invalid_colour_is_rejected_rather_than_guessed():
    with pytest.raises(ValueError):
        contrast_ratio("not-a-colour", "#000000")
