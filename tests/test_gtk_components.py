"""The GTK component sheet: the shared numbers, in both themes.

The card, button, field and sheet metrics live in ``design_components`` so the
two frontends cannot drift.  GTK's CSS has no length variables, so
``theme.component_css()`` renders those numbers into a provider of its own;
these tests read the numbers from the module and check they arrived, rather
than repeating a value here that could quietly disagree.

Skipped without GTK 3 or a display, like the rest of the GTK suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import design_components as dc
from lgl import design_tokens
from lgl import theme
from lgl import ui


def _display_ready() -> bool:
    try:
        ready, _argv = Gtk.init_check(None)
    except (TypeError, RuntimeError):  # pragma: no cover - no toolkit
        return False
    return bool(ready)


needs_display = pytest.mark.skipif(
    not _display_ready(), reason="the GTK component sheet needs a display"
)


def css_rules(css: str) -> dict[str, dict[str, str]]:
    """``{selector: {property: value}}`` for the simple rules we emit."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        declarations: dict[str, str] = {}
        for part in match.group(2).split(";"):
            if ":" in part:
                name, value = part.split(":", 1)
                declarations[name.strip()] = value.strip()
        for selector in (item.strip() for item in match.group(1).split(",")):
            if selector:
                rules.setdefault(selector, {}).update(declarations)
    return rules


COMPONENT_CSS = theme.component_css()
COMPONENT = css_rules(COMPONENT_CSS)


@pytest.fixture()
def app():
    """One themed application window, with the real sheets installed."""
    window = Gtk.Window()
    window.set_default_size(1000, 700)
    ui.load_css()
    window.show_all()
    for _ in range(5):
        while Gtk.events_pending():
            Gtk.main_iteration()
    yield window
    window.destroy()
    ui._COMPONENT_PROVIDER = None


# -- the numbers come from design_components -------------------------------


@pytest.mark.parametrize(
    "value,reason",
    (
        (dc.BUTTON_CONTENT, "button content height"),
        (dc.BUTTON_SMALL_CONTENT, "small button content height"),
        (dc.ICON_BUTTON_CONTENT, "icon button content height"),
        (dc.BUTTON_RADIUS, "button radius"),
        (dc.BUTTON_PADDING_X, "button padding"),
        (dc.BUTTON_LABEL_SIZE, "button label size"),
        (dc.INPUT_CONTENT, "field content height"),
        (dc.INPUT_RADIUS, "field radius"),
        (dc.INPUT_PADDING_X, "field padding"),
        (dc.INPUT_FOCUS_RING, "field focus ring"),
        (dc.FIELD_ERROR_SIZE, "field error size"),
        (dc.CARD_RADIUS, "card radius"),
        (dc.POSTER_RADIUS, "poster radius"),
        (dc.CARD_PADDING, "card padding"),
        (dc.CARD_FOCUS_RING, "card focus ring"),
        (dc.CARD_TITLE_SIZE, "card title size"),
        (dc.CARD_META_SIZE, "card meta size"),
        (dc.DIALOG_RADIUS, "sheet radius"),
        (dc.DIALOG_PADDING, "sheet padding"),
        (dc.DIALOG_GAP, "sheet gap"),
        (dc.DIALOG_HEADER_SIZE, "sheet header size"),
        (dc.DIALOG_TITLE_SIZE, "sheet title size"),
    ),
)
def test_a_shared_number_reaches_the_sheet(value, reason):
    assert f"{value}px" in COMPONENT_CSS, f"{reason} ({value}px) is not in the sheet"


def test_the_card_hover_and_selected_rules_use_the_shared_alphas():
    hover = COMPONENT["flowboxchild.library-tile .cover-card:hover"]
    assert hover["border-color"] == f"alpha(@accent, {dc.CARD_HOVER_BORDER_ALPHA:.2f})"
    assert hover["box-shadow"].startswith(f"0 {dc.CARD_LIFT}px")
    selected = COMPONENT["flowboxchild.library-tile:selected .cover-card"]
    assert selected["border"] == f"{dc.CARD_SELECTED_BORDER}px solid @accent"
    assert selected["background-color"] == f"alpha(@accent, {dc.CARD_SELECTED_ALPHA:.2f})"
    # The accent border is thicker than the plain one, so the padding gives the
    # difference back and the poster does not jump when a tile is selected.
    assert (
        dc.CARD_PADDING - int(selected["padding"].rstrip("px"))
        == dc.CARD_SELECTED_BORDER - dc.CARD_BORDER_WIDTH
    )


def test_every_card_class_is_on_the_same_surface():
    for selector in (
        "flowboxchild.library-tile .cover-card",
        ".browse-tile .cover-card",
        "button.movie-card",
        "button.feed-card",
    ):
        rule = COMPONENT[selector]
        assert rule["border-radius"] == f"{dc.CARD_RADIUS}px"
        assert rule["padding"] == f"{dc.CARD_PADDING}px"
        assert rule["border"] == (
            f"{dc.CARD_BORDER_WIDTH}px solid @{theme.css_name('divider')}"
        )


STYLE_CSS = (Path(__file__).resolve().parents[1] / "lgl" / "style.css").read_text(
    encoding="utf-8"
)


def test_every_sheet_names_only_published_tokens():
    """A name theme.py does not publish paints nothing, silently.

    Both sheets are checked: the shared ``style.css`` main keeps, and the
    component sheet this slice renders.  The published names come from
    ``theme.css_name``, which renames raised to overlay, divider to border,
    muted to text2 and faint to muted, so neither sheet may name the internal
    keys.
    """
    published = {theme.css_name(token) for token in theme.DARK}
    for label, text in (("style.css", STYLE_CSS), ("the component sheet", COMPONENT_CSS)):
        # ``@define-color`` is the provider syntax, not a token reference.
        used = set(re.findall(r"@([a-z_]+)", text)) - {"define"}
        assert used <= published, (
            f"{label} names tokens theme.py does not publish: "
            f"{sorted(used - published)}"
        )
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", COMPONENT_CSS) == [], (
        "literal colours in the component sheet"
    )


# -- the widgets measure what the table says -------------------------------


@needs_display
def test_a_primary_button_measures_thirty_six(app):
    button = Gtk.Button(label="Save")
    ui.style_button(button, "primary")
    app.add(button)
    app.show_all()
    _height = button.get_preferred_height()
    assert _height[0] == dc.BUTTON_HEIGHT, _height
    assert button.get_style_context().has_class("btn-primary")


@needs_display
def test_small_and_icon_buttons_measure_twenty_eight_and_thirty_six(app):
    small = Gtk.Button(label="More")
    ui.style_button(small, "secondary", small=True)
    icon = Gtk.Button()
    icon.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
    ui.style_button(icon, "quiet", icon=True, tooltip="Refresh")
    box = Gtk.Box()
    box.pack_start(small, False, False, 0)
    box.pack_start(icon, False, False, 0)
    app.add(box)
    app.show_all()
    assert small.get_preferred_height()[0] == dc.BUTTON_SMALL_HEIGHT
    assert icon.get_preferred_height()[0] == dc.ICON_BUTTON_SIZE
    assert icon.get_preferred_width()[0] == dc.ICON_BUTTON_SIZE
    # An icon-only button carries the name its picture cannot say.
    assert icon.get_tooltip_text() == "Refresh"
    assert icon.get_accessible().get_name() == "Refresh"


@needs_display
def test_the_four_button_kinds_are_the_only_ones(app):
    kinds = []
    for kind in dc.BUTTON_KINDS:
        button = Gtk.Button(label=kind)
        ui.style_button(button, kind)
        app.add(button)
        kinds.append(
            [name for name in button.get_style_context().list_classes() if name.startswith("btn-")]
        )
    app.show_all()
    assert kinds == [[f"btn-{kind}"] for kind in dc.BUTTON_KINDS]
    # A misspelt kind falls back to secondary instead of drawing an unstyled
    # button nobody notices.
    fallback = Gtk.Button(label="fallback")
    ui.style_button(fallback, "primry")
    assert "btn-secondary" in fallback.get_style_context().list_classes()


@needs_display
def test_an_entry_measures_thirty_six_and_shows_its_error(app):
    entry = Gtk.Entry()
    entry.set_placeholder_text("API key")
    app.add(entry)
    app.show_all()
    assert entry.get_preferred_height()[0] == dc.INPUT_HEIGHT

    label = ui.field_error_label("Needs a TMDB key")
    assert label.get_style_context().has_class("field-error-text")
    assert not label.get_visible()

    ui.set_field_error(entry, "Needs a TMDB key")
    assert entry.get_style_context().has_class("field-error")
    assert entry.get_tooltip_text() == "Needs a TMDB key"
    ui.clear_field_error(entry)
    assert not entry.get_style_context().has_class("field-error")
    assert entry.get_tooltip_text() is None


@needs_display
def test_check_boxes_draw_the_shared_indicator(app):
    check = Gtk.CheckButton(label="Remember")
    app.add(check)
    app.show_all()
    assert "checkbutton check" in COMPONENT_CSS
    assert f"{dc.CHECK_INDICATOR - 2 * dc.BORDER_WIDTH}px" in COMPONENT[
        "checkbutton check"
    ]["min-width"]


# -- dialogs ---------------------------------------------------------------


def build_dialog():
    """A sheet shaped like the app's: one field and a Cancel/Save pair."""
    dialog = Gtk.Dialog(title="Example", flags=0)
    dialog.set_default_size(420, -1)
    cancel = dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    save = dialog.add_button("Save", Gtk.ResponseType.OK)
    entry = Gtk.Entry()
    entry.set_placeholder_text("Name")
    dialog.get_content_area().pack_start(entry, False, False, 0)
    focus = ui.polish_dialog(dialog, primary=save, cancel=cancel)
    return dialog, cancel, save, entry, focus


@needs_display
def test_a_sheet_has_one_primary_button_and_starts_on_its_field(app):
    dialog, cancel, save, entry, focus = build_dialog()
    dialog.set_transient_for(app)
    dialog.show_all()
    for _ in range(5):
        while Gtk.events_pending():
            Gtk.main_iteration()
    assert dialog.get_style_context().has_class("sheet")
    assert save.get_style_context().has_class("btn-primary")
    assert cancel.get_style_context().has_class("btn-quiet")
    assert dialog.get_default_widget() is save
    assert focus is entry
    assert dialog.get_focus() is entry
    dialog.destroy()


def press(widget, keyval) -> bool:
    """Send one key press to ``widget`` the way the toolkit would.

    The event carries the widget's own GdkWindow: a bare Xvfb has no window
    manager, and an event built without a window is rejected by GTK's key
    handling, so this is what makes the check deterministic here and on CI.
    """
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = keyval
    event.window = widget.get_window()
    return bool(widget.emit("key-press-event", event))


def drain():
    for _ in range(5):
        while Gtk.events_pending():
            Gtk.main_iteration()


@needs_display
def test_escape_closes_a_sheet_and_enter_takes_the_primary(app):
    dialog, _cancel, save, entry, _focus = build_dialog()
    responses = []
    dialog.connect("response", lambda _d, response: responses.append(response))
    dialog.set_transient_for(app)
    dialog.show_all()
    dialog.present()
    drain()

    # Enter in the first field is the primary: GTK activates the sheet's default
    # widget (the primary button for the OK response), and the field is set up
    # so Return in it reaches that default instead of being swallowed.
    assert entry.get_activates_default()
    assert dialog.get_default_widget() is save
    assert dialog.get_widget_for_response(Gtk.ResponseType.OK) is save

    assert press(dialog, Gdk.KEY_Escape)
    drain()
    assert Gtk.ResponseType.CANCEL in responses, responses
    dialog.destroy()


@needs_display
def test_a_sheet_and_a_card_fit_a_nine_hundred_pixel_window():
    dialog, _cancel, _save, _entry, _focus = build_dialog()
    dialog.get_content_area().pack_start(
        Gtk.Label(
            label="A long explanation " * 6,
            wrap=True,
            max_width_chars=60,
            xalign=0,
        ),
        False,
        False,
        0,
    )
    dialog.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    width, _height = dialog.get_preferred_width()
    assert width <= dc.MIN_WINDOW_WIDTH, width
    dialog.destroy()


# -- contrast, both themes -------------------------------------------------

#: The two pairs the shared tokens cannot bring to 4.5:1.  Both come from
#: ``design_tokens.py``, which is read-only for this slice, so the ratios are
#: pinned here rather than skipped: a token change that fixes one of them fails
#: this table on purpose, and the table is then updated with the new number.
TOKEN_CLASHES: dict[tuple[str, str, str], float] = {
    # White on the dark accent fill: the accent itself is the fill colour.
    ("dark", "on_accent", "accent"): 4.23,
    # The light danger ink on a white surface.
    ("light", "danger", "surface"): 3.76,
}

#: Every other pair must reach AA.  ``danger_text`` is the shade the sheet
#: actually draws error text in, and it passes in both themes.
PAIRS = (
    ("text on page", "text", "bg"),
    ("text on surface", "text", "surface"),
    ("text on overlay", "text", "raised"),
    ("secondary text on page", "muted", "bg"),
    ("secondary text on surface", "muted", "surface"),
    ("muted text on page", "faint", "bg"),
    ("label on the accent fill", "on_accent", "accent_fill"),
    ("error text on surface", "danger_text", "surface"),
    ("error text on page", "danger_text", "bg"),
    ("clash: white on the dark accent", "on_accent", "accent"),
    ("clash: light danger on the surface", "danger", "surface"),
)


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("label,foreground,background", PAIRS)
def test_every_text_pair_meets_wcag_aa(label, foreground, background, mode):
    palette = theme.PALETTES[mode]
    ratio = design_tokens.contrast_ratio(palette[foreground], palette[background])
    pinned = TOKEN_CLASHES.get((mode, foreground, background))
    if pinned is not None:
        assert round(ratio, 2) == pinned, (
            f"{label} is {ratio:.2f}:1 in {mode} mode, not the pinned {pinned}:1 "
            "the shared tokens give it"
        )
        return
    assert ratio >= theme.CONTRAST_TEXT, f"{label} is {ratio:.2f}:1 in {mode} mode"


def test_the_clash_table_holds_exactly_the_two_shared_tokens():
    assert set(TOKEN_CLASHES) == {
        ("dark", "on_accent", "accent"),
        ("light", "danger", "surface"),
    }
    assert all(ratio < theme.CONTRAST_TEXT for ratio in TOKEN_CLASHES.values())


def blend(foreground: str, background: str, alpha: float) -> str:
    """``foreground`` at ``alpha`` drawn over ``background``, as the eye sees it."""
    top = [int(foreground[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    under = [int(background[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    mixed = [top[i] * alpha + under[i] * (1 - alpha) for i in range(3)]
    return "#%02x%02x%02x" % tuple(round(channel * 255) for channel in mixed)


#: Every new pair this slice introduces is a label over a fill the sheet paints,
#: so each one is measured against the blended fill, not the flat token.
FILL_PAIRS = (
    ("danger button label on its hover tint", "danger_text", "danger", 0.05, "surface"),
    ("danger button label on a sheet", "danger_text", None, 0.0, "surface"),
    ("quiet label on a sheet", "muted", None, 0.0, "surface"),
    ("selected card title on its fill", "text", "accent", 0.10, "surface"),
    ("selected card meta on its fill", "muted", "accent", 0.10, "surface"),
    ("secondary button label on its fill", "text", "text", 0.06, "surface"),
    ("hovered card title on its fill", "text", "text", 0.035, "surface"),
)


@pytest.mark.parametrize("mode", ("dark", "light"))
@pytest.mark.parametrize("label,foreground,fill,alpha,under", FILL_PAIRS)
def test_every_new_label_reads_on_the_fill_it_sits_on(
    label, foreground, fill, alpha, under, mode
):
    pal = theme.PALETTES[mode]
    background = pal[under] if not fill else blend(pal[fill], pal[under], alpha)
    ratio = design_tokens.contrast_ratio(pal[foreground], background)
    assert ratio >= theme.CONTRAST_TEXT, (
        f"{label} is {ratio:.2f}:1 in {mode} mode on {background}"
    )
