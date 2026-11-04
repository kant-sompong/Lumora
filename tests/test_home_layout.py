from lgl import home_layout as hl


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def test_defaults_show_every_shelf_in_order():
    layout = hl.HomeLayout(Store())
    assert layout.order() == hl.DEFAULT_ORDER
    assert layout.layout() == hl.POSTER
    assert layout.hidden() == []


def test_move_reorders_and_persists():
    settings = Store()
    layout = hl.HomeLayout(settings)
    assert layout.move("downloads", 0) is True
    assert layout.order()[0] == "downloads"
    assert hl.HomeLayout(settings).order()[0] == "downloads"
    assert layout.move("nope", 0) is False
    assert layout.move("downloads", "junk") is False


def test_hide_and_show():
    layout = hl.HomeLayout(Store())
    assert layout.hide("recent") is True
    assert "recent" not in layout.order()
    assert layout.hidden() == ["recent"]
    assert layout.show("recent") is True
    assert "recent" in layout.order()
    assert layout.hide("nope") is False


def test_the_last_visible_shelf_cannot_be_hidden():
    layout = hl.HomeLayout(Store())
    for shelf in hl.DEFAULT_ORDER[:-1]:
        assert layout.hide(shelf) is True
    assert layout.order() == [hl.DEFAULT_ORDER[-1]]
    assert layout.hide(hl.DEFAULT_ORDER[-1]) is False


def test_layout_choice_is_clamped():
    layout = hl.HomeLayout(Store())
    assert layout.set_layout(hl.COMPACT) == hl.COMPACT
    assert layout.set_layout("gigantic") == hl.POSTER
    assert hl.layout_of(None) == hl.POSTER


def test_reset_restores_defaults():
    settings = Store()
    layout = hl.HomeLayout(settings)
    layout.move("downloads", 0)
    layout.hide("recent")
    layout.set_layout(hl.COMPACT)
    layout.reset()
    assert layout.order() == hl.DEFAULT_ORDER
    assert layout.hidden() == []
    assert layout.layout() == hl.POSTER


def test_unknown_stored_shelves_are_ignored():
    settings = Store()
    settings.set(hl.SETTINGS_KEY, {"order": ["ghost", "downloads"], "hidden": ["ghost"]})
    layout = hl.HomeLayout(settings)
    assert "ghost" not in layout.order()
    assert layout.order()[0] == "downloads"


def test_as_dict_round_trip():
    layout = hl.HomeLayout(Store())
    snapshot = layout.as_dict()
    assert set(snapshot) == {"order", "hidden", "layout"}
    assert snapshot["order"] == hl.DEFAULT_ORDER
