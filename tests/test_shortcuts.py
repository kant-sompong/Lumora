import pytest

from lgl import shortcuts as sc


def test_every_shortcut_has_both_toolkit_spellings():
    ids = [item.id for item in sc.all_shortcuts()]
    assert len(ids) == len(set(ids)), "duplicate shortcut id"
    for item in sc.all_shortcuts():
        assert item.label and item.description
        assert item.keys_gtk and item.keys_qt
        assert item.keys(sc.GTK) == item.keys_gtk
        assert item.keys(sc.QT) == item.keys_qt


def test_search_and_navigation_shortcuts_exist():
    for name in (sc.SEARCH, sc.SETTINGS, sc.LIBRARY, sc.MEDIA, sc.DOWNLOADS, sc.SHORTCUT_HELP):
        assert sc.accelerator(name, sc.GTK)
    assert "<Control>f" in sc.accelerator(sc.SEARCH, sc.GTK)
    assert sc.accelerator(sc.SEARCH, sc.QT) == "Ctrl+F"


def test_unknown_shortcut_returns_empty():
    assert sc.lookup("nope") is None
    assert sc.accelerator("nope") == ""


def test_help_text_lists_groups_and_keys():
    text = sc.help_text(sc.QT)
    assert "Search" in text and "Ctrl+F" in text
    assert "Navigation" in text
    assert any(group for group, _ in sc.groups())


def test_accessible_labels_never_empty():
    assert sc.accessible("")["label"] == "Button"
    assert sc.accessible("", "Open settings")["label"] == "Open settings"
    pair = sc.accessible("Reload", "Reloads the page")
    assert pair["label"] == "Reload" and pair["description"].startswith("Reloads")
    assert len(pair["label"]) <= 120 and len(pair["description"]) <= 300


def test_focus_order_skips_unfocusable():
    class Widget:
        def __init__(self, name, can_focus=True):
            self.name = name
            self.can_focus = can_focus

    widgets = [Widget("a"), Widget("b", can_focus=False), Widget("c")]
    assert [w.name for w in sc.focus_order(widgets)] == ["a", "c"]


def test_contrast_math_matches_wcag_reference():
    assert sc.contrast_ratio("#ffffff", "#000000") == 21.0
    assert sc.contrast_ratio("#000000", "#000000") == 1.0
    assert sc.contrast_ratio("#fff", "#000") == 21.0
    # An unparsable colour must not raise; it reads as black.
    assert sc.contrast_ratio("not-a-colour", "#000000") == 1.0
    assert sc.contrast_ratio("", "") == 1.0


def test_readable_text_picks_the_better_extreme():
    assert sc.readable_text_on("#ffffff") == "#111111"
    assert sc.readable_text_on("#101010") == "#ffffff"


def test_check_theme_reports_only_failures():
    problems = sc.check_theme(
        [
            ("#111111", "#ffffff"),
            ("#777777", "#888888"),
        ]
    )
    assert len(problems) == 1
    assert "#777777" in problems[0]
    assert sc.check_theme([("#111111", "#ffffff")], minimum=3.0) == []
