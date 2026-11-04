"""The stylesheets must keep a visible focus ring on interactive tiles."""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "lgl" / "style.css"


def _blocks(source: str) -> list[tuple[str, str]]:
    return [(selector.strip(), body) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source)]


def test_tiles_that_suppress_the_outline_restore_it_for_keyboard_focus() -> None:
    blocks = _blocks(CSS.read_text("utf-8"))
    suppressed = [selector.split()[0] for selector, body in blocks if "outline: none" in body]
    assert suppressed, "expected the custom tiles to set outline: none"

    focus_selectors = " ".join(
        selector for selector, body in blocks if "outline" in body and ":focus" in selector
    )
    for selector in suppressed:
        assert selector in focus_selectors, f"{selector} hides the focus ring without drawing one"


def test_restored_focus_ring_has_a_width_and_an_offset() -> None:
    source = CSS.read_text("utf-8")
    assert "outline: 2px solid;" in source
    assert "outline-offset: 2px;" in source
