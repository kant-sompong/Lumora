"""Per-section Home sizes: round-trip, old layouts, and reset."""

from __future__ import annotations

from lgl import home_layout


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


def test_a_section_size_round_trips_through_the_settings():
    settings = Store()
    layout = home_layout.HomeLayout(settings)

    assert layout.size("continue") == home_layout.SIZE_DEFAULT
    assert layout.set_size("continue", "large") == "large"

    reloaded = home_layout.HomeLayout(settings)
    assert reloaded.size("continue") == "large"
    assert settings["home_layout"]["sizes"]["continue"] == "large"


def test_an_old_layout_without_sizes_still_loads():
    settings = Store({
        "home_layout": {
            "order": ["recent", "continue"],
            "hidden": ["favorites"],
            "layout": "large",
        }
    })
    layout = home_layout.HomeLayout(settings)

    assert layout.order()[0] == "recent"
    assert "favorites" not in layout.order()
    assert layout.layout() == "large"
    for shelf in home_layout.DEFAULT_ORDER:
        assert layout.size(shelf) == "normal"


def test_reset_restores_order_visibility_and_sizes():
    settings = Store()
    layout = home_layout.HomeLayout(settings)
    layout.set_size("continue", "compact")
    layout.set_size("recent", "large")
    layout.hide("favorites")
    layout.move("downloads", 0)

    layout.reset()

    fresh = home_layout.HomeLayout(settings)
    assert fresh.order() == home_layout.DEFAULT_ORDER
    assert fresh.hidden() == []
    assert {fresh.size(shelf) for shelf in home_layout.DEFAULT_ORDER} == {"normal"}


def test_unknown_values_and_shelves_are_ignored():
    settings = Store()
    layout = home_layout.HomeLayout(settings)

    assert layout.set_size("not-a-shelf", "large") == ""
    assert home_layout.size_of("Huge") == "normal"
    assert home_layout.size_of(None) == "normal"


def test_sizes_change_height_and_how_many_titles_fit():
    assert home_layout.size_height("compact") < home_layout.size_height("normal")
    assert home_layout.size_height("normal") < home_layout.size_height("large")
    assert home_layout.shelf_titles("compact") < home_layout.shelf_titles("large")
    assert home_layout.shelf_for_label("Favourites") == "favorites"
    assert home_layout.shelf_for_label("Co-op night") == ""
