import pytest

from lgl import game_sources as gs


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def test_sources_separate_installed_from_catalogue():
    installed = [s for s in gs.sources() if s.installed]
    catalogue = [s for s in gs.sources() if not s.installed]
    assert installed and catalogue
    assert [s.key for s in catalogue] == ["catalogue"]


def test_optional_sources_need_setup_and_stay_off_until_configured():
    settings = Store()
    states = {state.key: state for state in gs.setup_state(settings)}
    assert states["steam"].state == "needs-folder"
    assert states["catalogue"].state == "available"
    assert gs.path_for(settings, "steam") is None
    assert gs.set_path(settings, "catalogue", "/nope") is False


def test_path_must_exist_to_be_ready(tmp_path):
    settings = Store()
    assert gs.set_path(settings, "steam", tmp_path / "missing") is True
    assert {s.key: s for s in gs.setup_state(settings)}["steam"].state == "missing-folder"
    real = tmp_path / "SteamLibrary"
    real.mkdir()
    gs.set_path(settings, "steam", real)
    assert {s.key: s for s in gs.setup_state(settings)}["steam"].state == "ready"


def test_sources_can_be_switched_off():
    settings = Store()
    assert gs.set_enabled(settings, "epic", False) is True
    assert gs.is_enabled(settings, "epic") is False
    assert {s.key: s for s in gs.setup_state(settings)}["epic"].state == "off"
    assert gs.set_enabled(settings, "catalogue", False) is False


def test_classify_installed_versus_catalogue():
    assert gs.classify({"title": "Heat", "installed": True}) == gs.INSTALLED
    assert gs.classify({"title": "Heat", "source": "steam"}) == gs.INSTALLED
    assert gs.classify({"title": "Heat", "source": "catalogue"}) == gs.CATALOGUE
    assert gs.classify({"title": "Heat"}) == gs.CATALOGUE


def test_merge_prefers_the_installed_record():
    installed = [{"identifier": "portal", "title": "Portal", "installed": True, "path": "/games/portal"}]
    catalogue = [
        {"identifier": "portal", "title": "Portal", "year": 2007},
        {"identifier": "hl2", "title": "Half-Life 2", "year": 2004},
    ]
    merged, duplicates = gs.merge(installed, catalogue)
    assert duplicates == 1
    by_id = {entry["identifier"]: entry for entry in merged}
    assert by_id["portal"]["origin"] == gs.INSTALLED
    assert by_id["portal"]["path"] == "/games/portal"
    assert merged[0]["origin"] == gs.INSTALLED
    assert len(merged) == 2


def test_merge_ignores_nameless_rows():
    merged, duplicates = gs.merge([{"path": "/x"}], [])
    assert merged == [] and duplicates == 0


def test_limitations_mention_no_credentials():
    text = " ".join(gs.limitations()).lower()
    assert "no storefront api" in text or "no storefront" in text
    assert "credentials" in text
