"""The Qt 18+ badge: only on a card TMDB flags adult, and drawn from tokens."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_media  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def tmdb_item(**kwargs) -> SimpleNamespace:
    base = dict(key="movie:900", title="Overflow", year="2020",
                kind="movie", tmdb_id=900, poster_url="", rating=None,
                adult=True)
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_an_adult_tmdb_card_shows_the_badge(app):
    card = qt_media.PosterCard(qt_media.Card(
        key="movie:900", title="Overflow", adult=True,
    ))

    assert card.adult_badge.text() == "18+"
    assert card.adult_badge.parent() is card.cover
    assert card.adult_badge.toolTip() == "Marked adult (18+) by TMDB"


def test_a_regular_tmdb_card_shows_no_badge(app):
    card = qt_media.PosterCard(qt_media.Card(key="movie:1", title="A Film"))

    assert not hasattr(card, "adult_badge")


def test_the_badge_is_styled_from_the_shared_palette(app):
    card = qt_media.PosterCard(qt_media.Card(key="movie:900", title="Overflow",
                                             adult=True))
    sheet = card.adult_badge.styleSheet()

    assert "background-color:" in sheet
    assert "danger" not in sheet  # a token name would not render; colours do
    assert "border-radius:" in sheet


def test_card_from_tmdb_keeps_the_adult_flag():
    assert qt_media.card_from_tmdb(tmdb_item(adult=True)).adult is True
    assert qt_media.card_from_tmdb(tmdb_item(adult=False)).adult is False


def test_a_stub_without_the_field_is_not_adult():
    item = tmdb_item()
    del item.adult

    assert qt_media.card_from_tmdb(item).adult is False
