"""Library folders, file inspection and the library index."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lgl.library import (
    game_folder,
    inspect_file,
    scan_game_folder,
    write_manifest,
)
from lgl.library_store import (
    DEFAULT_STATUS,
    LibraryEntry,
    LibraryStore,
    human_size,
)


def item(**kw):
    base = dict(
        identifier="demo_game", title="Demo Game", creator="Tester",
        date="2020-01-01", description="A test item.", licenseurl="",
        rights="", subjects=["Action"], download_url="https://archive.org/details/x",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# folders and manifests
# --------------------------------------------------------------------------


def test_game_folder_is_created_and_identifiers_are_validated(tmp_path):
    folder = game_folder(tmp_path, "demo_game")
    assert folder.is_dir()
    with pytest.raises(ValueError):
        game_folder(tmp_path, "../escape")
    with pytest.raises(ValueError):
        game_folder(tmp_path, "")


def test_manifest_records_provenance_and_is_owned_by_lumora(tmp_path):
    path = write_manifest(tmp_path, item())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["identifier"] == "demo_game"
    assert data["title"] == "Demo Game"
    assert data["source_url"] == "https://archive.org/details/x"
    assert data["managed_by"] == "Lumora"


# --------------------------------------------------------------------------
# inspecting files
# --------------------------------------------------------------------------


def test_documents_are_not_launchable(tmp_path):
    path = tmp_path / "readme.txt"
    path.write_text("hello", encoding="utf-8")
    local = inspect_file(path)
    assert local.document is True
    assert local.launchable is False
    assert local.size == 5


def test_shell_scripts_are_launchable(tmp_path):
    path = tmp_path / "run.sh"
    path.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    local = inspect_file(path)
    assert local.launchable is True
    assert "Shell" in local.kind


def test_magic_bytes_beat_an_ambiguous_suffix(tmp_path):
    path = tmp_path / "game.bin"
    path.write_bytes(b"\x7fELF" + bytes(64))
    local = inspect_file(path)
    assert local.kind == "ELF executable"
    assert local.launchable is True


def test_scan_skips_hidden_partial_and_app_owned_files(tmp_path):
    folder = tmp_path / "game"
    folder.mkdir()
    (folder / "game").write_bytes(b"\x7fELF" + bytes(10))
    (folder / ".hidden").write_text("x", encoding="utf-8")
    (folder / "big.zip.part").write_bytes(b"x")
    (folder / "game.json").write_text("{}", encoding="utf-8")
    (folder / "launch.log").write_text("x", encoding="utf-8")
    (folder / "data").mkdir()
    (folder / "data" / "inner.dat").write_bytes(b"data")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (folder / "link.txt").symlink_to(outside)

    scanned = scan_game_folder(folder)
    names = {Path(f.relpath).name for f in scanned.files}
    assert names == {"game", "inner.dat"}
    assert [Path(f.relpath).name for f in scanned.executables] == ["game"]


def test_scanning_a_missing_folder_is_empty_not_an_error(tmp_path):
    scanned = scan_game_folder(tmp_path / "nope")
    assert scanned.files == [] and scanned.executables == []


# --------------------------------------------------------------------------
# entries and storage
# --------------------------------------------------------------------------


def test_from_dict_never_raises_on_junk():
    entry = LibraryEntry.from_dict("not a mapping")
    assert entry.identifier == "" and entry.status == DEFAULT_STATUS

    entry = LibraryEntry.from_dict({
        "identifier": "x", "title": "X", "status": "bogus",
        "year": "1999", "genres": ["Action", "Action"], "notes": "n" * 5000,
        "size": -5, "favorite": "yes",
    })
    assert entry.status == DEFAULT_STATUS
    assert entry.year == "1999"
    assert entry.genres == ("Action",)
    assert len(entry.notes) == 4000
    assert entry.size == 0
    assert entry.favorite is True


def test_round_trip_keeps_every_field():
    entry = LibraryEntry(
        identifier="x", title="X", year="2001", genres=("Action",),
        platforms=("Windows",), favorite=True,
        magnet="magnet:?xt=urn:btih:" + "a" * 40,
        infohash="a" * 40, magnet_origin="extracted",
    )
    copy = LibraryEntry.from_dict(entry.to_dict())
    assert copy.to_dict() == entry.to_dict()


def test_store_keeps_entries_and_saves_atomically(tmp_path):
    path = tmp_path / "library.json"
    store = LibraryStore(path)
    store.upsert(LibraryEntry(identifier="a", title="A"))
    assert store.save() is True
    assert store.get("a") is not None and store.get("a").title == "A"

    again = LibraryStore(path)
    assert [e.identifier for e in again.entries()] == ["a"]

    store.remove("a")
    assert store.get("a") is None
    assert store.save() is True
    assert LibraryStore(path).entries() == []


def test_in_memory_store_never_writes(tmp_path):
    store = LibraryStore()
    store.upsert(LibraryEntry(identifier="a"))
    assert store.save() is False


def test_favorites_can_be_toggled():
    store = LibraryStore()
    store.upsert(LibraryEntry(identifier="a"))
    assert store.toggle_favorite("a") is True
    assert store.toggle_favorite("a") is False
    assert store.toggle_favorite("missing") is None
    assert store.set_favorite("a", True) is True
    assert store.get("a").favorite is True


def test_export_and_import_json(tmp_path):
    store = LibraryStore()
    store.upsert(LibraryEntry(identifier="a", title="A", favorite=True))
    export = tmp_path / "export.json"
    assert store.export_json(export) == 1

    other = LibraryStore()
    report = other.import_json(export)
    assert other.get("a") is not None
    assert other.get("a").favorite is True
    assert report is not None


def test_import_of_a_broken_file_is_reported_not_raised(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    store = LibraryStore()
    report = store.import_json(path)
    assert report.imported == 0
    assert report.errors
    assert store.entries() == []


def test_scan_indexes_a_downloaded_folder(tmp_path):
    root = tmp_path / "downloads"
    folder = root / "demo_game"
    folder.mkdir(parents=True)
    write_manifest(folder, item())
    (folder / "game").write_bytes(b"\x7fELF" + bytes(32))
    (folder / "cover.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + bytes(64)
    )

    store = LibraryStore(download_root=root)
    report = store.scan()
    assert report.added == 1
    entry = store.get("demo_game")
    assert entry is not None
    assert entry.title == "Demo Game"
    assert entry.status == "installed"
    assert entry.file_count == 2
    assert entry.size > 0


def test_scan_marks_a_vanished_folder_as_missing(tmp_path):
    root = tmp_path / "downloads"
    folder = root / "demo_game"
    folder.mkdir(parents=True)
    write_manifest(folder, item())
    (folder / "game").write_bytes(b"\x7fELF" + bytes(4))

    store = LibraryStore(download_root=root)
    store.scan()
    assert store.get("demo_game").status == "installed"

    for child in folder.iterdir():
        child.unlink()
    folder.rmdir()
    store.scan()
    assert store.get("demo_game").status == "missing"


def test_human_size_is_readable():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KiB"
    assert human_size(1024 * 1024) == "1.0 MiB"
