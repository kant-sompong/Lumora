"""Media Home sections 1-6: injected loaders, hiding and failure lines."""

from __future__ import annotations

import os
import time

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


def cards(count: int, kind: str = "movie"):
    return [
        qt_media.Card(key=f"{kind}:{n}", title=f"Film {n}", kind=kind, year="2024")
        for n in range(1, count + 1)
    ]


def feed(tmp_path):
    return qt_media.MediaPage(settings=None, cache_root=tmp_path)


def test_each_row_takes_its_cards_from_its_loader(app, tmp_path):
    page = feed(tmp_path)
    page.set_home_loaders({
        "continue": lambda: cards(3),
        "trending": lambda: cards(4),
        "for_you": lambda: cards(5),
        "hidden_gems": lambda: cards(2, "anime"),
        "more_like": lambda: cards(2),
    })
    page.refresh_home()
    wait(app)

    assert len(page._home_rows["continue"].cards()) == 3
    assert len(page._home_rows["trending"].cards()) == 4
    assert len(page._home_rows["for_you"].cards()) == 5
    assert len(page._home_rows["hidden_gems"].cards()) == 2
    assert len(page._home_rows["more_like"].cards()) == 2
    assert page._home_rows["trending"].isVisibleTo(page)


def test_a_row_with_nothing_to_show_hides_itself(app, tmp_path):
    page = feed(tmp_path)
    page.set_home_loaders({"continue": lambda: [], "for_you": lambda: []})
    page.refresh_home()
    wait(app)

    assert not page._home_rows["continue"].isVisibleTo(page)
    assert not page._home_rows["for_you"].isVisibleTo(page)


def test_a_failed_row_shows_one_dim_line_and_does_not_raise(app, tmp_path):
    page = feed(tmp_path)

    def boom():
        raise RuntimeError("AniList timed out")

    page.set_home_loaders({"hidden_gems": boom, "more_like": boom})
    page.refresh_home()
    wait(app)

    row = page._home_rows["hidden_gems"]
    assert row.error.text() == "Could not load: AniList timed out"
    assert row.isVisibleTo(page)
    assert row.posters() == []


def test_sections_do_not_block_the_page(app, tmp_path):
    page = feed(tmp_path)
    slow_returned = []

    def slow():
        time.sleep(0.4)
        slow_returned.append(True)
        return cards(2)

    page.set_home_loaders({"trending": slow})
    started = time.time()
    page.refresh_home()
    elapsed = time.time() - started

    assert elapsed < 0.2, "refresh_home must return before the loader finishes"
    assert page._home_rows["trending"].placeholder.isVisibleTo(page._home_rows["trending"])
    wait(app, 3.0)
    assert slow_returned


def test_hidden_gems_and_anime_gems_are_one_row(app, tmp_path):
    page = feed(tmp_path)
    assert "anime_gems" not in page._home_rows
    assert "Anime gems" in page._home_rows["hidden_gems"].heading.note.text()


def test_a_gem_entry_becomes_a_poster_card():
    card = qt_media.MediaPage._card_from_gem({
        "id": 21, "title": "Keroro", "score": 82, "popularity": 900,
    })
    assert card.key == "anime:21"
    assert card.kind == "anime"
    assert card.title == "Keroro"
    assert card.rating == pytest.approx(8.2)
