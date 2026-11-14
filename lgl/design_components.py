"""Component metrics shared by both frontends: cards, buttons, inputs, sheets.

``design_tokens`` owns the palette, the spacing steps, the radii and the type
sizes.  This module owns the *component* numbers built out of them - how tall a
button is, how much padding a card has, how thick a focus ring is - so the Qt
stylesheet and the GTK stylesheet can be measured against the same table instead
of two sets of magic numbers that drift apart.

Nothing here imports a GUI toolkit, and every value either comes from
:mod:`lgl.design_tokens` or is a small derived number with its reason written
beside it.

Heights are expressed two ways on purpose: ``*_HEIGHT`` is what the widget
measures on screen, ``*_CONTENT`` is the box the stylesheet sets because Qt adds
the border to a ``min-height``.  A one pixel border therefore gives
``BUTTON_HEIGHT = BUTTON_CONTENT + 2 * BORDER_WIDTH``.
"""

from __future__ import annotations

from .design_tokens import RADIUS, SPACING, TEXT_SIZES

#: One pixel, the only border width in the component set.
BORDER_WIDTH = 1

# -- cards ------------------------------------------------------------------

#: Cards are the middle radius step, and the poster inside them is round too.
CARD_RADIUS = RADIUS[1]
POSTER_RADIUS = RADIUS[1]
#: 12 px of breathing room inside a card.
CARD_PADDING = SPACING[2]
#: The card's own margin between the poster and its two text lines.
CARD_TEXT_GAP = SPACING[1]
CARD_BORDER_WIDTH = BORDER_WIDTH
#: Hover turns the border into the accent at 55 % opacity; the fill stays.
CARD_HOVER_BORDER_ALPHA = 0.55
#: Selected means a 2 px accent border plus the accent-soft wash.
CARD_SELECTED_BORDER = 2
CARD_SELECTED_ALPHA = 0.10
#: Keyboard focus is a 2 px accent ring and is never drawn away.
CARD_FOCUS_RING = 2
#: Hover lifts the card two pixels by its shadow.  It never changes geometry,
#: so the grid does not reflow under the pointer.
CARD_LIFT = 2
CARD_TITLE_SIZE = TEXT_SIZES["label"]
CARD_META_SIZE = TEXT_SIZES["caption"]

# -- buttons ----------------------------------------------------------------

BUTTON_HEIGHT = 36
BUTTON_SMALL_HEIGHT = 28
BUTTON_RADIUS = RADIUS[0]
BUTTON_PADDING_X = SPACING[3]
BUTTON_LABEL_SIZE = TEXT_SIZES["label"]
#: 36 x 36 icon-only buttons, with an accessible name and a tooltip.
ICON_BUTTON_SIZE = 36
#: The stylesheet's content box: the height minus the one pixel border above
#: and below.
BUTTON_CONTENT = BUTTON_HEIGHT - 2 * BORDER_WIDTH
BUTTON_SMALL_CONTENT = BUTTON_SMALL_HEIGHT - 2 * BORDER_WIDTH
ICON_BUTTON_CONTENT = ICON_BUTTON_SIZE - 2 * BORDER_WIDTH

#: The three kinds, plus the danger variant reserved for destructive actions.
BUTTON_KINDS: tuple[str, ...] = ("primary", "secondary", "quiet", "danger")
#: Only one primary button per dialog or page section.
PRIMARY_PER_SECTION = 1

# -- inputs -----------------------------------------------------------------

INPUT_HEIGHT = 36
INPUT_RADIUS = RADIUS[0]
INPUT_PADDING_X = SPACING[2]
INPUT_CONTENT = INPUT_HEIGHT - 2 * BORDER_WIDTH
#: Focus is a 1 px accent border with a soft accent ring drawn inside it.
INPUT_FOCUS_BORDER = 1
INPUT_FOCUS_RING = 2
INPUT_FOCUS_ALPHA = 0.18
#: A field that cannot be used shows the danger colour, never the accent.
INPUT_ERROR_BORDER = 1
CHECK_INDICATOR = 18
CHECK_RADIUS = RADIUS[0]
FIELD_ERROR_SIZE = TEXT_SIZES["caption"]

# -- dialogs ----------------------------------------------------------------

DIALOG_RADIUS = RADIUS[2]
#: 24 px of padding and 16 px between blocks, per the sheet spec.
DIALOG_PADDING = SPACING[4]
DIALOG_GAP = SPACING[3]
DIALOG_HEADER_SIZE = TEXT_SIZES["header"]
DIALOG_TITLE_SIZE = TEXT_SIZES["title"]
#: The narrowest window the layout is promised to survive.
MIN_WINDOW_WIDTH = 900


def derived_ok() -> bool:
    """Every component number sits on the shared scales.

    Used by the tests and by anyone reading this file: a value that is not a
    token step is a bug, not a nuance.
    """
    radii = set(RADIUS)
    steps = set(SPACING)
    sizes = set(TEXT_SIZES.values())
    return all(
        (
            CARD_RADIUS in radii,
            POSTER_RADIUS in radii,
            CARD_PADDING in steps,
            CARD_TEXT_GAP in steps,
            CARD_TITLE_SIZE in sizes,
            CARD_META_SIZE in sizes,
            BUTTON_RADIUS in radii,
            BUTTON_PADDING_X in steps,
            BUTTON_LABEL_SIZE in sizes,
            INPUT_RADIUS in radii,
            INPUT_PADDING_X in steps,
            DIALOG_RADIUS in radii,
            DIALOG_PADDING in steps,
            DIALOG_GAP in steps,
            DIALOG_HEADER_SIZE in sizes,
            DIALOG_TITLE_SIZE in sizes,
            FIELD_ERROR_SIZE in sizes,
        )
    )


__all__ = [
    "BORDER_WIDTH",
    "BUTTON_CONTENT",
    "BUTTON_HEIGHT",
    "BUTTON_KINDS",
    "BUTTON_LABEL_SIZE",
    "BUTTON_PADDING_X",
    "BUTTON_RADIUS",
    "BUTTON_SMALL_CONTENT",
    "BUTTON_SMALL_HEIGHT",
    "CARD_BORDER_WIDTH",
    "CARD_FOCUS_RING",
    "CARD_HOVER_BORDER_ALPHA",
    "CARD_LIFT",
    "CARD_META_SIZE",
    "CARD_PADDING",
    "CARD_RADIUS",
    "CARD_SELECTED_ALPHA",
    "CARD_SELECTED_BORDER",
    "CARD_TEXT_GAP",
    "CARD_TITLE_SIZE",
    "CHECK_INDICATOR",
    "CHECK_RADIUS",
    "DIALOG_GAP",
    "DIALOG_HEADER_SIZE",
    "DIALOG_PADDING",
    "DIALOG_RADIUS",
    "DIALOG_TITLE_SIZE",
    "FIELD_ERROR_SIZE",
    "ICON_BUTTON_CONTENT",
    "ICON_BUTTON_SIZE",
    "INPUT_CONTENT",
    "INPUT_ERROR_BORDER",
    "INPUT_FOCUS_ALPHA",
    "INPUT_FOCUS_BORDER",
    "INPUT_FOCUS_RING",
    "INPUT_HEIGHT",
    "INPUT_PADDING_X",
    "INPUT_RADIUS",
    "MIN_WINDOW_WIDTH",
    "POSTER_RADIUS",
    "PRIMARY_PER_SECTION",
    "derived_ok",
]
