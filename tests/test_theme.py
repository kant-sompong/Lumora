"""Colour tokens must stay readable in every palette."""

import pytest

from lgl import theme


def test_every_palette_meets_wcag_aa_for_body_text() -> None:
    for name in ("dark", "light"):
        colors = theme.palette(name)
        pairs = [
            (colors["text"], colors["bg"]),
            (colors["text"], colors["surface"]),
            (colors["muted"], colors["bg"]),
            (colors["muted"], colors["surface"]),
            (colors["accent_text"], colors["bg"]),
            (colors["accent_text"], colors["surface"]),
            (colors["on_accent"], colors["accent_fill"]),
        ]
        problems = [
            f"{f'{fg} on {bg}':<28} {theme.contrast_ratio(fg, bg):.2f}:1"
            for fg, bg in pairs
            if theme.contrast_ratio(fg, bg) < 4.5
        ]
        assert problems == [], f"{name} palette below AA:\n" + "\n".join(problems)


@pytest.mark.parametrize("mode", theme.MODES)
def test_every_mode_resolves_to_a_real_palette(mode: str) -> None:
    for system_dark in (True, False):
        resolved = theme.resolve(mode, system_dark=system_dark)
        assert resolved in ("dark", "light")
        assert theme.palette(resolved)["text"]
