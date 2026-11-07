import json

import pytest

from lgl import profiles


@pytest.fixture()
def store(tmp_path):
    return profiles.Profiles(tmp_path / profiles.PROFILES_FILE)


def test_fresh_store_has_one_default_profile(store):
    assert [p.id for p in store.list()] == [profiles.DEFAULT_ID]
    assert store.active().name == profiles.DEFAULT_NAME
    assert "1 profile" in store.label()


def test_create_rename_and_activate(store):
    made = store.create("Alex")
    assert made is not None and made.id == "alex"
    assert store.create("alex") is None  # duplicate name
    assert store.create("   ") is None
    assert store.activate(made.id) is True
    assert store.active_id == "alex"
    assert store.activate("ghost") is False
    assert store.rename(made.id, "Alexandra") is True
    assert store.rename(made.id, "Default") is False  # clashes with the other profile
    assert store.get(made.id).name == "Alexandra"


def test_profiles_are_capped(store):
    for i in range(profiles.MAX_PROFILES - 1):
        assert store.create(f"Person {i}") is not None
    assert store.create("One too many") is None


def test_last_profile_cannot_be_removed(store):
    assert store.remove(profiles.DEFAULT_ID) is False
    other = store.create("Alex")
    assert store.activate(other.id) is True
    assert store.remove(profiles.DEFAULT_ID) is True
    assert store.active_id == other.id
    assert store.remove(other.id) is False


def test_data_directories_are_isolated(store, tmp_path):
    other = store.create("Alex")
    default_dir = store.data_dir(tmp_path)
    alex_dir = store.data_dir(tmp_path, other.id)
    assert default_dir != alex_dir
    assert alex_dir.name == "alex"
    store.activate(other.id)
    assert store.data_dir(tmp_path) == alex_dir


def test_state_survives_reload(store, tmp_path):
    other = store.create("Alex")
    store.activate(other.id)
    reloaded = profiles.Profiles(tmp_path / profiles.PROFILES_FILE)
    assert reloaded.active_id == "alex"
    assert len(reloaded.list()) == 2


def test_corrupt_file_falls_back_to_default(tmp_path):
    path = tmp_path / profiles.PROFILES_FILE
    path.write_text("{not json", "utf-8")
    store = profiles.Profiles(path)
    assert [p.id for p in store.list()] == [profiles.DEFAULT_ID]
    path.write_text(json.dumps({"active": "ghost", "profiles": [{"id": "a", "name": "A"}]}), "utf-8")
    store = profiles.Profiles(path)
    assert store.active_id == "a"


def test_export_import_roundtrip(store, tmp_path):
    store.create("Alex")
    document = store.export()
    fresh = profiles.Profiles(tmp_path / "other" / profiles.PROFILES_FILE)
    assert fresh.import_(document) == 1
    assert fresh.import_(document) == 0
    assert {p.name for p in fresh.list()} == {"Default", "Alex"}


def test_security_note_is_explicit():
    assert "not accounts" in profiles.SECURITY_NOTE
    assert "not a security boundary" in profiles.SECURITY_NOTE
