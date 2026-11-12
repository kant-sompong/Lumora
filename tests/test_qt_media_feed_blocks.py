"""Sections 7-9: three-column lists, the schedule strip and the compare card."""

from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_media  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, seconds: float = 2.0) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def page(tmp_path):
    return qt_media.MediaPage(settings=None, cache_root=tmp_path)


def test_new_release_and_completed_hide_on_their_own_errors(app, tmp_path):
    media = page(tmp_path)
    media.set_column_loaders({
        "new_release": lambda: [{"title": "Episode 4", "meta": "AniList · 12 Mar"}],
        "new_added": lambda: [],
        "just_completed": lambda: (_ for _ in ()).throw(RuntimeError("AniList timed out")),
    })
    media.refresh_columns()
    wait(app)

    assert media.three_columns.rows("new_release") == 1
    assert media.three_columns.rows("new_added") == 0
    assert media.three_columns.error_text("just_completed") == "Could not load: AniList timed out"


def test_schedule_strip_takes_entries_from_its_loader(app, tmp_path):
    media = page(tmp_path)
    next_day = datetime.now().replace(hour=9, minute=5, second=0, microsecond=0)
    media.set_schedule_loader(lambda: [
        {"title": "Keroro", "episode": 7, "air_at": next_day.timestamp()},
    ])
    media.refresh_schedule()
    wait(app)

    assert media.schedule.rows() == 1
    assert media.schedule.isVisibleTo(media)
    names = [
        widget.findChildren(type(media.schedule.now_label))[0].text()
        for widget in [media.schedule.list_rows.itemAt(i).widget()
                       for i in range(media.schedule.rows())]
    ]
    assert "09:05" in names


def test_schedule_failure_shows_one_dim_line_and_no_strip(app, tmp_path):
    media = page(tmp_path)

    def boom():
        raise RuntimeError("AniList unreachable")

    media.set_schedule_loader(boom)
    media.refresh_schedule()
    wait(app)

    assert not media.schedule.isVisibleTo(media)
    assert media.schedule_error.text() == "Could not load: AniList unreachable"
    assert media.schedule_error.isVisibleTo(media)


def test_the_home_page_has_no_season_comparison_card(app, tmp_path):
    media = page(tmp_path)

    assert not hasattr(media, "compare_card")
    assert not hasattr(media, "compare_panel")
