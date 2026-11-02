"""Tests for the 0.6.0 rename: Lumora folders, copy-on-first-launch, rollback.

Every test runs against a temporary HOME with its own XDG or AppData folders,
so nothing here touches the real user profile.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lgl import migration, platform_support
from lgl.settings import KEYRING_SERVICE, LEGACY_KEYRING_SERVICES, Settings


def linux_env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }


def windows_env(tmp_path: Path) -> dict[str, str]:
    return {
        "USERPROFILE": str(tmp_path / "home"),
        "APPDATA": str(tmp_path / "roaming"),
        "LOCALAPPDATA": str(tmp_path / "local"),
    }


def write_legacy_linux(tmp_path: Path, env: dict[str, str]) -> tuple[Path, Path]:
    config = Path(env["XDG_CONFIG_HOME"]) / "linux-game-library"
    data = Path(env["XDG_DATA_HOME"]) / "linux-game-library"
    (config).mkdir(parents=True)
    (data / "exports").mkdir(parents=True)
    (config / "config.json").write_text(
        json.dumps({"version": 4, "theme": "dark", "download_dir": "/games"})
    )
    (data / "library.json").write_text(
        json.dumps({"version": 2, "entries": [{"identifier": "demo"}]})
    )
    (data / "downloads.json").write_text(json.dumps({"tasks": []}))
    (data / "torrents.json").write_text(json.dumps({"magnets": []}))
    (data / "catalog.json").write_text(json.dumps({"games": []}))
    (data / "exports" / "report.csv").write_text("title,size\nDemo,1\n")
    cache = Path(env["XDG_CACHE_HOME"]) / "linux-game-library" / "covers"
    cache.mkdir(parents=True)
    (cache / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return config, data


# --------------------------------------------------------------------------
# folders
# --------------------------------------------------------------------------

def test_new_folders_use_the_lumora_names():
    linux = linux_env(Path("/tmp/lumora-names"))
    assert platform_support.config_dir("linux", linux).name == "lumora"
    assert platform_support.data_dir("linux", linux).name == "lumora"
    assert platform_support.cache_dir("linux", linux).name == "lumora"

    windows = windows_env(Path("/tmp/lumora-names"))
    assert platform_support.config_dir("win32", windows).name == "Lumora"
    assert platform_support.data_dir("win32", windows).name == "Lumora"
    assert platform_support.cache_dir("win32", windows).name == "Cache"
    assert platform_support.cache_dir("win32", windows).parent.name == "Lumora"


def test_legacy_folders_are_still_discoverable():
    linux = linux_env(Path("/tmp/lumora-legacy"))
    assert platform_support.legacy_config_dir("linux", linux).name == "linux-game-library"
    assert platform_support.legacy_data_dir("linux", linux).name == "linux-game-library"
    windows = windows_env(Path("/tmp/lumora-legacy"))
    assert platform_support.legacy_config_dir("win32", windows).name == "NativeGameLibrary"


def test_migration_copies_settings_library_and_queues(tmp_path):
    env = linux_env(tmp_path)
    old_config, old_data = write_legacy_linux(tmp_path, env)

    report = migration.migrate_once(system="linux", env=env)

    assert report.migrated is True
    assert report.errors == []
    new_config = platform_support.config_dir("linux", env)
    new_data = platform_support.data_dir("linux", env)
    assert json.loads((new_config / "config.json").read_text())["theme"] == "dark"
    assert json.loads((new_data / "library.json").read_text())["entries"]
    for name in ("downloads.json", "torrents.json", "catalog.json"):
        assert (new_data / name).is_file(), name
    assert (new_data / "exports" / "report.csv").read_text().startswith("title,size")
    # Nothing was moved or deleted from the old folders.
    assert (old_config / "config.json").is_file()
    assert (old_data / "library.json").is_file()
    assert report.copied


def test_migration_leaves_the_cache_behind(tmp_path):
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)

    migration.migrate_once(system="linux", env=env)

    new_cache = platform_support.cache_dir("linux", env)
    assert not (new_cache / "covers" / "cover.png").exists()


def test_second_launch_does_not_overwrite_newer_data(tmp_path):
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)
    migration.migrate_once(system="linux", env=env)
    new_config = platform_support.config_dir("linux", env) / "config.json"
    new_config.write_text(json.dumps({"version": 4, "theme": "light"}))

    report = migration.migrate_once(system="linux", env=env)

    assert report.migrated is False
    assert json.loads(new_config.read_text())["theme"] == "light"


def test_existing_lumora_data_wins_over_old_folders(tmp_path):
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)
    new_config = platform_support.config_dir("linux", env)
    new_config.mkdir(parents=True)
    (new_config / "config.json").write_text(json.dumps({"theme": "light"}))

    report = migration.migrate_once(system="linux", env=env)

    assert report.migrated is False
    assert "already hold" in report.reason
    assert json.loads((new_config / "config.json").read_text())["theme"] == "light"


def test_no_old_folders_is_a_quiet_no_op(tmp_path):
    env = linux_env(tmp_path)
    report = migration.migrate_once(system="linux", env=env)
    assert report.migrated is False
    assert "No folders" in report.reason
    assert not migration.marker_path_for("linux", env).exists()


def test_marker_records_what_happened_and_how_to_roll_back(tmp_path):
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)
    migration.migrate_once(system="linux", env=env)

    document = json.loads(migration.marker_path_for("linux", env).read_text())

    assert document["status"] == "copied"
    assert document["from"]["config"].endswith("linux-game-library")
    assert document["to"]["config"].endswith("lumora")
    assert "delete" in document["rollback"]
    assert migration.last_report("linux", env)["status"] in ("copied", "skipped")


def test_rollback_then_upgrade_copies_again(tmp_path):
    """Deleting the new folders must return the user to the old data."""
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)
    migration.migrate_once(system="linux", env=env)

    shutil.rmtree(platform_support.config_dir("linux", env))
    shutil.rmtree(platform_support.data_dir("linux", env))
    assert (Path(env["XDG_CONFIG_HOME"]) / "linux-game-library" / "config.json").is_file()

    report = migration.migrate_once(system="linux", env=env)

    assert report.migrated is True
    assert json.loads(
        (platform_support.data_dir("linux", env) / "library.json").read_text()
    )["entries"]


def test_a_failed_file_is_reported_without_stopping_the_rest(tmp_path, monkeypatch):
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)
    real_copy2 = shutil.copy2

    def flaky(source, target, *args, **kwargs):
        if str(source).endswith("downloads.json"):
            raise OSError("disk on fire")
        return real_copy2(source, target, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", flaky)
    report = migration.migrate_once(system="linux", env=env)

    assert report.migrated is True
    assert any("downloads.json" in error for error in report.errors)
    assert (platform_support.config_dir("linux", env) / "config.json").is_file()
    assert not (
        platform_support.data_dir("linux", env) / "downloads.json"
    ).exists()


def test_windows_folders_migrate_from_native_game_library(tmp_path):
    env = windows_env(tmp_path)
    old_config = Path(env["APPDATA"]) / "NativeGameLibrary"
    old_data = Path(env["LOCALAPPDATA"]) / "NativeGameLibrary"
    old_config.mkdir(parents=True)
    old_data.mkdir(parents=True)
    (old_config / "config.json").write_text(json.dumps({"version": 4}))
    (old_data / "library.json").write_text(json.dumps({"version": 2, "entries": []}))

    report = migration.migrate_once(system="win32", env=env)

    assert report.migrated is True
    assert (Path(env["APPDATA"]) / "Lumora" / "config.json").is_file()
    assert (Path(env["LOCALAPPDATA"]) / "Lumora" / "library.json").is_file()
    assert (old_config / "config.json").is_file()


def test_ensure_app_dirs_migrates_on_first_launch(tmp_path, monkeypatch):
    env = linux_env(tmp_path)
    write_legacy_linux(tmp_path, env)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    platform_support.ensure_app_dirs()

    new_config = platform_support.config_dir()
    assert (new_config / "config.json").is_file()
    assert (new_config / "migration.json").is_file()
    assert platform_support.cache_dir().is_dir()


# --------------------------------------------------------------------------
# keyring
# --------------------------------------------------------------------------

class FakeKeyring:
    def __init__(self, entries: dict[tuple[str, str], str] | None = None) -> None:
        self.entries = dict(entries or {})

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.entries.pop((service, username), None)


def test_a_key_saved_under_the_old_service_is_copied(tmp_path):
    keyring = FakeKeyring({(LEGACY_KEYRING_SERVICES[0], "tmdb"): "abc123"})
    settings = Settings(tmp_path / "config.json", keyring=keyring)

    assert settings.has_api_key("tmdb") is True
    assert settings.api_key("tmdb") == "abc123"
    assert keyring.entries[(KEYRING_SERVICE, "tmdb")] == "abc123"
    # The old entry stays, so a rollback still finds the key.
    assert keyring.entries[(LEGACY_KEYRING_SERVICES[0], "tmdb")] == "abc123"


def test_a_new_key_wins_over_the_old_service(tmp_path):
    keyring = FakeKeyring({
        (KEYRING_SERVICE, "rawg"): "new-key",
        (LEGACY_KEYRING_SERVICES[0], "rawg"): "old-key",
    })
    settings = Settings(tmp_path / "config.json", keyring=keyring)

    assert settings.api_key("rawg") == "new-key"


def test_clearing_a_key_removes_it_from_both_services(tmp_path):
    keyring = FakeKeyring({
        (KEYRING_SERVICE, "mal"): "client-id",
        (LEGACY_KEYRING_SERVICES[0], "mal"): "client-id",
    })
    settings = Settings(tmp_path / "config.json", keyring=keyring)

    settings.clear_api_key("mal")

    assert settings.api_key("mal") == ""
    assert (KEYRING_SERVICE, "mal") not in keyring.entries
    assert (LEGACY_KEYRING_SERVICES[0], "mal") not in keyring.entries
