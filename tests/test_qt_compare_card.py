"""The compact Compare seasons card: one picker row, then three short columns."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QBoxLayout, QLabel  # noqa: E402

from lgl import qt_media, qt_season, season_data  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds: float = 0.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def entries(count: int, prefix: str) -> list[dict]:
    return [
        {
            "key": f"anilist:{prefix.lower()}{index}",
            "title": f"{prefix} {index}",
            "score": 80,
            "format": "TV",
        }
        for index in range(count)
    ]


def result(new: int = 0, left: int = 0, carried: int = 0,
           error: str = "", first: str = "Summer 2026",
           second: str = "Spring 2026") -> season_data.CompareResult:
    compared = season_data.CompareResult(
        first_label=first,
        second_label=second,
        result={
            "only_first": entries(new, "New"),
            "only_second": entries(left, "Left"),
            "shared": entries(carried, "Carry"),
        },
    )
    if error:
        compared.errors = [error]
    return compared


def card(app, _loader=None) -> qt_season.CompareCard:
    made = qt_season.CompareCard(threaded=False, loader=_loader or (lambda *a: result(1, 1, 1)))
    made.show()
    pump(app)
    return made


def test_before_compare_only_the_picker_row_is_there(app):
    widget = card(app)

    assert widget.title.text() == "Compare seasons"
    assert widget.subtitle.text() == (
        "See what is new, what left and what carried over between two seasons."
    )
    assert widget.picker_row.isVisibleTo(widget)
    for hidden in (widget.summary, widget.columns_host, widget.loading, widget.error_row):
        assert not hidden.isVisibleTo(widget)
    assert widget.compare_btn.text() == "Compare"
    assert widget.swap_btn.isVisibleTo(widget)
    # Compact: no room is reserved for three empty result boxes.
    assert widget.sizeHint().height() < 240


def test_the_default_pair_is_this_season_against_the_previous_one(app):
    widget = card(app)

    season, year = season_data.latest_season()
    before, before_year = season_data.previous_season(season, year)
    assert widget.values() == (season, year, before, before_year)
    assert widget.labels() == (season_data.season_label(season, year),
                               season_data.season_label(before, before_year))


def test_the_swap_button_flips_a_and_b(app):
    widget = card(app)
    before = widget.values()

    widget.swap_btn.click()

    assert widget.values() == (before[2], before[3], before[0], before[1])


def test_the_chips_pick_the_common_pairs(app):
    widget = card(app)

    season, year = season_data.latest_season()
    before, before_year = season_data.previous_season(season, year)
    widget.chip_last.click()
    assert widget.values() == (season, year, before, before_year)

    widget.chip_year.click()
    season, year = season_data.latest_season()
    assert widget.values() == (season, year, season, year - 1)


def test_the_same_season_twice_disables_compare_and_says_why(app):
    widget = card(app)

    widget.combo_b.setCurrentIndex(widget.combo_a.currentIndex())

    assert widget.same_selection()
    assert not widget.compare_btn.isEnabled()
    assert widget.hint.isVisibleTo(widget)
    assert widget.hint.text() == "Pick two different seasons."


def test_the_summary_counts_new_left_and_carried_over(app):
    widget = card(app)

    widget.set_result(result(new=14, left=9, carried=21))

    texts = [label.text() for label in widget.count_labels]
    assert texts == [
        "14 new in Summer 2026",
        "9 only in Spring 2026",
        "21 carried over",
    ]
    assert widget.summary.isVisibleTo(widget)


def test_a_column_shows_five_rows_and_expands_in_place(app):
    widget = card(app)
    widget.set_result(result(new=14))

    assert widget.rows("only_first") == 5
    more = widget.show_all_button("only_first")
    assert more.isVisibleTo(widget)
    assert more.text() == "Show all 14"

    more.click()

    assert widget.rows("only_first") == 14
    assert not more.isVisibleTo(widget)


def test_a_row_opens_the_title(app):
    widget = card(app)
    opened: list[object] = []
    widget.item_clicked.connect(opened.append)
    widget.set_result(result(new=2))

    row = widget._column_rows["only_first"].itemAt(0).widget()
    row.clicked.emit(row.item)

    assert opened
    assert opened[0]["title"] == "New 0"
    assert opened[0]["key"] == "anilist:new0"
    assert opened[0]["kind"] == "anime"


def test_loading_is_one_dim_line_with_both_seasons(app):
    seen: list[str] = []
    widget = None

    def loader(*_args):
        seen.append(widget.loading.text())
        return result(new=1)

    widget = card(app, loader)
    widget.compare()

    season, year = season_data.latest_season()
    before, before_year = season_data.previous_season(season, year)
    expected = (f"Comparing {season_data.season_label(season, year)} and "
                f"{season_data.season_label(before, before_year)}...")
    assert seen == [expected]
    assert not widget.loading.isVisibleTo(widget)
    assert widget.summary.isVisibleTo(widget)


def test_a_failure_is_one_line_and_a_retry(app):
    calls = {"count": 0}

    def loader(*_args):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("AniList timed out")
        return result(new=3)

    widget = card(app, loader)
    widget.compare()

    assert widget.error.text() == "Could not load: AniList timed out"
    assert widget.error_row.isVisibleTo(widget)
    assert widget.retry.isVisibleTo(widget)
    assert not widget.columns_host.isVisibleTo(widget)

    widget.retry.click()

    assert widget.summary.isVisibleTo(widget)
    assert not widget.error_row.isVisibleTo(widget)


def test_nothing_compared_yet_means_no_columns_even_after_an_empty_result(app):
    widget = card(app)
    widget.set_result(result())

    # Nothing on either side: no tall empty boxes, only the counts say zero.
    for key in widget.COLUMNS:
        assert not widget._column_frames[key].isVisibleTo(widget)
    assert widget.rows("only_first") == 0
    assert [label.text() for label in widget.count_labels] == [
        "0 new in Summer 2026",
        "0 only in Spring 2026",
        "0 carried over",
    ]
    # No "Nothing to compare" wording anywhere in this card.
    texts = [label.text() for label in widget.findChildren(type(widget.title))]
    assert "Nothing to compare" not in texts


def test_the_columns_stack_when_the_card_is_narrow(app):
    widget = card(app)
    widget.resize(1200, 700)
    pump(app)
    assert widget._columns_row.direction() == QBoxLayout.LeftToRight

    widget.resize(600, 700)
    pump(app)
    assert widget._columns_row.direction() == QBoxLayout.TopToBottom


def test_the_menu_dialog_hosts_the_card(app):
    dialog = qt_season.CompareCardDialog(threaded=False,
                                         loader=lambda *a: result(new=2))
    dialog.show()
    pump(app)

    assert isinstance(dialog.card, qt_season.CompareCard)
    assert dialog.windowTitle() == "Compare seasons"
    # The old window is not built inside the menu's dialog any more.
    assert not dialog.findChildren(qt_season.SeasonCompareDialog)

    dialog.compare()
    pump(app)

    assert dialog.card.summary.isVisibleTo(dialog)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Nothing to compare" not in texts
    dialog.close()


def test_media_home_hosts_the_new_card_below_the_schedule(app, tmp_path):
    media = qt_media.MediaPage(settings=None, cache_root=tmp_path)

    assert isinstance(media.compare_panel, qt_season.CompareCard)
    assert media.compare_panel.close_btn is None
    assert media.compare_panel.parent() is media.compare_card
    layout = media.compare_card.parentWidget().layout()
    assert layout.indexOf(media.compare_card) > layout.indexOf(media.schedule)
    media.close()
