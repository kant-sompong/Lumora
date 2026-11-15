"""Hero inks must read on the hero in both themes.

The hero is dark artwork in both themes, so every label and control drawn on it
has to use the light hero set instead of the page's ``@text``/``@text2`` tokens.
These tests read the colours GTK actually resolves and require 4.5:1 against
the hero's darkest pixels.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 not installed
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import design_tokens, movies_ui, theme_gtk  # noqa: E402

CSS = Path(__file__).resolve().parent.parent / "lgl" / "style.css"

#: The scrim's bottom colour, which is what the hero inks sit on.
HERO_BG = "#0a0b0d"
MIN_RATIO = 4.5


def has_display() -> bool:
    return bool(Gtk.init_check()[0])


needs_display = pytest.mark.skipif(not has_display(), reason="no display for GTK")


def as_hex(colour) -> str:
    return "#%02x%02x%02x" % (
        round(colour.red * 255),
        round(colour.green * 255),
        round(colour.blue * 255),
    )


def over_hero(colour) -> str:
    """``colour`` blended over the hero, as the eye sees it."""
    if colour.alpha >= 0.999:
        return as_hex(colour)
    hero = [
        int(HERO_BG[1:3], 16) / 255,
        int(HERO_BG[3:5], 16) / 255,
        int(HERO_BG[5:7], 16) / 255,
    ]
    blended = [
        colour.red * colour.alpha + hero[0] * (1 - colour.alpha),
        colour.green * colour.alpha + hero[1] * (1 - colour.alpha),
        colour.blue * colour.alpha + hero[2] * (1 - colour.alpha),
    ]
    return "#%02x%02x%02x" % tuple(round(value * 255) for value in blended)


def ratio(widget, state) -> float:
    context = widget.get_style_context()
    foreground = context.get_color(state)
    background = context.get_background_color(state)
    return design_tokens.contrast_ratio(
        as_hex(foreground), over_hero(background)
    )


@pytest.fixture(scope="module", autouse=True)
def themed():
    if not has_display():
        pytest.skip("no display for GTK")
    theme_gtk.install()
    yield
    theme_gtk.apply("dark")


@pytest.fixture
def hero():
    """A hero action row plus the labels the hero draws."""
    window = Gtk.Window()
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    window.add(body)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.get_style_context().add_class("movie-hero-actions")
    body.pack_start(row, False, False, 0)

    primary = Gtk.Button(label="Watch")
    primary.get_style_context().add_class("movie-btn-primary")
    row.pack_start(primary, False, False, 0)
    dark = Gtk.Button(label="Back")
    dark.get_style_context().add_class("movie-btn-dark")
    row.pack_start(dark, False, False, 0)
    spin = Gtk.SpinButton.new_with_range(1, 10, 1)
    row.pack_start(spin, False, False, 0)

    labels = {}
    for name, klass in (
        ("meta", "movie-hero-meta"),
        ("overview", "movie-hero-overview"),
        ("title", "movie-hero-title"),
    ):
        label = Gtk.Label(label="2023")
        label.get_style_context().add_class(klass)
        body.pack_start(label, False, False, 0)
        labels[name] = label
    window.show_all()
    return {"window": window, "primary": primary, "dark": dark, "spin": spin,
            "labels": labels}


def pump() -> None:
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


@pytest.mark.parametrize("theme", ["light", "dark"])
@needs_display
def test_every_hero_label_clears_4_5_to_1(hero, theme):
    theme_gtk.apply(theme)
    pump()
    for name, label in hero["labels"].items():
        for label_name, state in (
            ("normal", Gtk.StateFlags.NORMAL),
            ("backdrop", Gtk.StateFlags.BACKDROP),
        ):
            found = ratio(label, state)
            assert found >= MIN_RATIO, (
                f"{name} label in {theme} ({label_name}) is {found:.2f}:1"
            )


@pytest.mark.parametrize("theme", ["light", "dark"])
@needs_display
def test_the_hero_buttons_and_picker_clear_4_5_to_1(hero, theme):
    theme_gtk.apply(theme)
    pump()
    for name in ("primary", "dark", "spin"):
        for state_name, state in (
            ("normal", Gtk.StateFlags.NORMAL),
            ("backdrop", Gtk.StateFlags.BACKDROP),
        ):
            found = ratio(hero[name], state)
            assert found >= MIN_RATIO, (
                f"{name} button in {theme} ({state_name}) is {found:.2f}:1"
            )


@needs_display
def test_the_hero_combo_uses_a_light_ink():
    window = Gtk.Window()
    row = Gtk.Box()
    row.get_style_context().add_class("movie-hero-actions")
    combo = Gtk.ComboBoxText()
    combo.append("sub", "Sub")
    combo.set_active_id("sub")
    row.pack_start(combo, False, False, 0)
    window.add(row)
    window.show_all()
    text = Path(CSS).read_text()
    block = re.search(
        r"\.movie-hero-actions combobox button,[^{]*\{([^}]*)\}", text
    )
    assert block is not None
    colour = re.search(r"color:\s*(#[0-9a-fA-F]{6})", block.group(1))
    assert colour is not None
    assert design_tokens.contrast_ratio(colour.group(1), HERO_BG) >= MIN_RATIO


def test_the_hero_rules_never_use_the_page_tokens():
    text = Path(CSS).read_text()
    rules = re.findall(
        r"\.movie-hero-actions[^{]*\{[^}]*\}", text
    )
    assert rules
    for rule in rules:
        assert "@text" not in rule
        assert "@text2" not in rule
        assert "@surface" not in rule
        assert "@muted" not in rule


@needs_display
def test_a_refill_keeps_the_hero_ink(hero):
    """Details arriving later must not repaint a hero line in the page ink."""
    box = Gtk.Box()
    stub = type("Stub", (), {})()
    stub._fill_meta = movies_ui.MoviesWindow._fill_meta.__get__(stub)
    stub._fill_meta(
        box, ["2023", "TV"], 8.4,
        css="movie-hero-meta", star_css="movie-hero-rating",
    )
    stub._fill_meta(box, ["2024", "TV"], 8.6)

    classes = set()
    for child in box.get_children():
        classes.update(child.get_style_context().list_classes())
    assert "movie-hero-meta" in classes
    assert "movie-hero-rating" in classes
    assert "movie-meta" not in classes


@needs_display
def test_the_season_header_has_no_box_when_open_or_pressed():
    window = Gtk.Window()
    picker = movies_ui.SeasonPicker(lambda _number: None)
    window.add(picker)
    window.show_all()
    theme_gtk.apply("light")
    pump()
    context = picker.get_style_context()
    for state in (Gtk.StateFlags.ACTIVE, Gtk.StateFlags.CHECKED,
                  Gtk.StateFlags.BACKDROP):
        colour = context.get_background_color(state)
        assert colour.alpha == 0.0, (
            f"the season header paints a box in state {int(state)}"
        )
