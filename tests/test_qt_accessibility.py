"""Icon-only widgets must expose a name, not just a tooltip."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_widgets  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.mark.parametrize(
    "factory",
    [
        lambda: qt_widgets.IconButton("search", "Search"),
        lambda: qt_widgets.DirectionButton("Sort direction"),
        lambda: qt_widgets.ViewScaleButton("Grid / list"),
    ],
)
def test_icon_only_widgets_have_an_accessible_name(app, factory) -> None:
    widget = factory()
    assert widget.toolTip()
    assert widget.accessibleName() == widget.toolTip()


def test_view_switch_relabels_itself_when_the_icon_changes(app) -> None:
    button = qt_widgets.ViewScaleButton()
    button.set_list(True)
    assert button.accessibleName() == "Show covers"
    button.set_list(False)
    assert button.accessibleName() == "Show as a list"
