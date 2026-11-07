"""Switching profiles really changes the files both apps read and write.

The frontends resolve their settings, library index, collections and history
through :mod:`lgl.profiles` at startup, so these tests do the same two things a
restart does: activate a profile, then open the stores again.  The Default
profile keeps the folders every install already has; the others get their own.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lgl import library_tools, platform_support, profiles, recently_viewed
from lgl.library_store import LibraryEntry, LibraryStore
from lgl.settings import Settings


@pytest.fixture()
def folders(tmp_path, monkeypatch):
    """A throwaway data and config folder, the way XDG would give one."""
    data = tmp_path / "data"
    config = tmp_path / "config"
    data.mkdir()
    config.mkdir()
    monkeypatch.setattr(platform_support, "data_dir", lambda *a, **k: data)
    monkeypatch.setattr(platform_support, "config_dir", lambda *a, **k: config)
    return data, config


def open_startup_files():
    """What both frontends open at startup, for the active profile."""
    settings = Settings(path=profiles.active_settings_path())
    library = LibraryStore(profiles.active_file("library.json"), None)
    return settings, library


def test_the_default_profile_keeps_todays_folders(folders):
    data, config = folders
    assert profiles.load().active_id == profiles.DEFAULT_ID

    # Nothing is moved or copied: Default reads exactly what it always read.
    assert profiles.active_data_dir() == data
    assert profiles.active_file("library.json") == data / "library.json"
    assert profiles.active_settings_path() == config / "config.json"
    assert not (data / profiles.PROFILE_ROOT).exists()


def test_a_favourite_a_tag_and_history_stay_in_their_profile(folders):
    data, _config = folders
    store = profiles.load()
    work = store.create("Work")
    assert work is not None
    assert store.activate(work.id) is True

    # Work profile: one favourite with a tag, one opened title.
    settings, library = open_startup_files()
    library.upsert(LibraryEntry(identifier="doom", title="Doom", folder=str(data / "Doom")))
    library.set_favorite("doom", True)
    assert library.save() is True
    library_tools.add_tag(settings, "doom", "co-op")
    recently_viewed.record(
        recently_viewed.default_path(), kind="game", key="doom", title="Doom"
    )
    settings.set("window_width", 1234)

    assert library_tools.tags_for(settings, "doom") == ("co-op",)
    assert recently_viewed.recent(recently_viewed.default_path())

    # Home profile starts empty: same code, different folder.
    home = store.create("Home")
    assert store.activate(home.id) is True
    other_settings, other_library = open_startup_files()
    assert other_library.entries() == []
    assert other_library.get("doom") is None
    assert library_tools.tags_for(other_settings, "doom") == ()
    assert recently_viewed.recent(recently_viewed.default_path()) == []
    assert other_settings.get("window_width") is None

    # And switching back sees everything again.
    assert store.activate(work.id) is True
    back_settings, back_library = open_startup_files()
    entry = back_library.get("doom")
    assert entry is not None
    assert entry.favorite is True
    assert library_tools.tags_for(back_settings, "doom") == ("co-op",)
    assert [item["key"] for item in recently_viewed.recent(
        recently_viewed.default_path()
    )] == ["doom"]
    assert back_settings.get("window_width") == 1234


def test_settings_stay_in_their_profile(folders):
    data, config = folders
    store = profiles.load()
    first = store.create("First")
    second = store.create("Second")
    assert first is not None and second is not None

    store.activate(first.id)
    first_path = profiles.active_settings_path()
    settings = Settings(path=first_path)
    settings.set("theme", "dark")
    settings.set("download_retries", 7)

    store.activate(second.id)
    second_path = profiles.active_settings_path()
    assert second_path != first_path
    other = Settings(path=second_path)
    assert other.get("theme") is None
    assert other.get("download_retries") is None

    store.activate(profiles.DEFAULT_ID)
    # The Default profile is still the install's single config file.
    assert profiles.active_settings_path() == config / "config.json"
    assert Settings(path=profiles.active_settings_path()).get("theme") is None

    store.activate(first.id)
    assert Settings(path=first_path).get("theme") == "dark"
    # Each profile's settings live beside its own library, not in the config dir.
    assert first_path.parent == data / profiles.PROFILE_ROOT / first.id


def test_deleting_or_renaming_never_touches_files(folders):
    data, _config = folders
    store = profiles.load()

    # The Default profile has data of its own.
    default_library = data / "library.json"
    default_library.write_text("{}", "utf-8")

    work = store.create("Work")
    assert work is not None
    folder = store.data_dir(data, work.id)
    folder.mkdir(parents=True)
    (folder / "library.json").write_text("{}", "utf-8")
    download = data / "downloads" / "Doom" / "doom.bin"
    download.parent.mkdir(parents=True)
    download.write_bytes(b"game data")

    assert store.rename(work.id, "Workshop") is True
    assert folder.is_dir() and (folder / "library.json").is_file()
    assert download.is_file()

    assert store.remove(work.id) is True
    assert store.get(work.id) is None
    # Deleting a profile removes only its entry: folder, files and games stay.
    assert folder.is_dir() and (folder / "library.json").is_file()
    assert download.is_file()
    assert default_library.is_file()


def test_a_running_app_keeps_its_profile_until_restart(folders):
    data, _config = folders
    store = profiles.load()
    work = store.create("Work")
    assert work is not None
    assert store.activate(work.id) is True

    # Startup pins the folder the whole run uses.
    profiles.start_session()
    try:
        work_dir = profiles.active_data_dir()
        assert work_dir == data / profiles.PROFILE_ROOT / work.id

        # Switching inside the running app records the choice, but the window
        # that is already open must not start reading the other profile.
        assert profiles.load().activate(profiles.DEFAULT_ID) is True
        assert profiles.active_data_dir() == work_dir
        assert profiles.active_settings_path() == work_dir / profiles.CONFIG_FILENAME
        assert profiles.active_file("library.json") == work_dir / "library.json"
    finally:
        profiles.reset_session()

    # The next start uses the profile that was chosen.
    assert profiles.active_data_dir() == data


def test_qt_header_names_the_profile_and_a_switch_asks_for_restart(folders):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from lgl import qt_main, qt_profiles

    app = QApplication.instance() or QApplication([])
    store = profiles.load()
    work = store.create("Work")
    assert work is not None
    assert store.activate(work.id) is True

    settings, library = open_startup_files()
    window = qt_main.Window(store=library, settings=settings)
    try:
        assert "Work" in window.profile_label.text()

        dialog = qt_profiles.ProfilesDialog(store, window, on_switch=window._profile_switched)
        default_id = profiles.DEFAULT_ID
        for row in range(dialog.list.count()):
            if dialog.list.item(row).data(256) == default_id:
                dialog.list.setCurrentRow(row)
        assert dialog.switch() is True
        assert "Restart Lumora to switch to Default" in dialog.note.text()
        # The header follows the choice, and the running window keeps its stores.
        assert "Default" in window.profile_label.text()
        assert "Work" in window.profile_label.text()  # where it came from
        assert store.active_id == profiles.DEFAULT_ID

        # The Default profile can never be deleted from the dialog.
        assert dialog.delete() is False
        assert store.get(default_id) is not None
        assert not (folders[0] / profiles.PROFILE_ROOT / profiles.DEFAULT_ID).exists()
    finally:
        window.close()
        app.processEvents()
