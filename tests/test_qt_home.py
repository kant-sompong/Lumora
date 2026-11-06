"""Home page shelves and the three card sizes."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from lgl import home_layout, qt_browse, shelves, tool_panels  # noqa: E402
from lgl.archive import GameSummary  # noqa: E402


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def game(title, subjects):
    return GameSummary(f"id-{title}", title, "2004", 0, None, subjects=list(subjects))


def test_a_saved_shelf_draws_its_name_and_a_title(app):
    settings = Store()
    shelves.add(settings, "Co-op night", {"genres": ["rpg"]}, 4)
    page = qt_browse.BrowsePage(settings=settings)
    page.set_games([game("Keroro", ["RPG"]), game("Other", ["Puzzle"])])

    headings = [w.text() for w in page.shelves_host.findChildren(QLabel)]
    assert "CO-OP NIGHT" in headings
    pills = [w.text() for w in page.shelves_host.findChildren(QPushButton)]
    assert "Keroro" in pills
    assert "Other" not in pills
    assert not page.shelves_host.isHidden()


def test_removing_the_shelf_hides_the_row(app):
    settings = Store()
    key = shelves.add(settings, "Co-op night", {"genres": ["rpg"]})
    page = qt_browse.BrowsePage(settings=settings)
    page.set_games([game("Keroro", ["RPG"])])
    assert not page.shelves_host.isHidden()

    assert shelves.remove(settings, key)
    page.refresh()
    assert page.shelves_host.isHidden()


def test_a_page_without_settings_has_no_shelves(app):
    page = qt_browse.BrowsePage()
    page.set_games([game("Keroro", ["RPG"])])
    assert page.shelves_host.isHidden()


def test_the_third_card_size_is_accepted_and_ordered():
    assert home_layout.LAYOUTS == (home_layout.POSTER, home_layout.LARGE, home_layout.COMPACT)
    assert home_layout.scale("large") > home_layout.scale("poster") > home_layout.scale("compact")
    layout = home_layout.HomeLayout(Store())
    assert layout.set_layout("large") == "large"
    assert home_layout.HomeLayout(layout.settings).layout() == "large"


def test_the_tools_home_section_offers_the_shelves_editor():
    section = tool_panels._home(Store())
    assert any(row.action == "home.shelves" for row in section.rows)
    choices = next(row for row in section.rows if row.action == "home.layout").options
    assert [value for value, _label in choices] == list(home_layout.LAYOUTS)
