"""Backups: what goes in, what is refused, what a restore would change."""

from __future__ import annotations

import json
import zipfile

import pytest

from lgl.backup import (
    BackupError,
    create_backup,
    inspect,
    load,
    restore_plan,
    sanitize_settings,
)


def test_settings_are_stripped_of_credentials():
    cleaned, removed = sanitize_settings(
        {"api_keys": {"tmdb": "secret"}, "download_dir": "/games"}
    )
    assert "api_keys" not in cleaned
    assert cleaned["download_dir"] == "/games"
    assert removed == ["api_keys"]


def test_round_trip(tmp_path):
    target = tmp_path / "b.zip"
    report = create_backup(
        target,
        library={"entries": [{"identifier": "a"}]},
        collections={"collections": []},
        settings={"api_keys": {"tmdb": "secret"}, "theme": "dark"},
        app_version="0.6.4",
    )
    assert report.ok and report.parts == ["library", "collections", "settings"]
    assert report.removed_secrets == ["api_keys"]

    info = inspect(target)
    assert info.ok and info.app_version == "0.6.4"
    documents = load(target)
    assert "api_keys" not in json.dumps(documents)
    assert documents["settings"]["theme"] == "dark"


def test_empty_selection_is_an_error(tmp_path):
    report = create_backup(tmp_path / "b.zip", include=())
    assert report.ok is False
    assert report.errors


def test_a_damaged_part_is_refused(tmp_path):
    target = tmp_path / "b.zip"
    assert create_backup(target, library={"entries": []}).ok
    with zipfile.ZipFile(target) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    entries["library.json"] = b'{"entries": ["tampered"]}'
    with zipfile.ZipFile(target, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    info = inspect(target)
    assert info.ok is False
    assert any("checksum" in error for error in info.errors)
    with pytest.raises(BackupError):
        load(target)
    assert manifest["format"] == "lumora-backup"


def test_not_a_backup(tmp_path):
    target = tmp_path / "plain.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("hello.txt", "hi")
    info = inspect(target)
    assert info.ok is False and info.errors
    with pytest.raises(BackupError):
        load(target)


def test_restore_plan_warns_about_overwrites():
    plan = restore_plan({"library": True}, {"library": {}, "collections": {}})
    assert plan == {"library": "overwrite", "collections": "add"}
