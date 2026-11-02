"""Pure rules of the archive client: URLs, posters and cache round trips."""

from __future__ import annotations

import pytest

from lgl.archive import (
    ArchiveFile,
    ArchiveItem,
    _game_from_doc,
    _item_from_cache,
    _item_to_dict,
    build_download_url,
)


def file(name, size=100, format="", private=False, **kw):
    return ArchiveFile(name=name, size=size, format=format, private=private, **kw)


def item(**kw):
    base = dict(
        identifier="demo_game",
        title="Demo Game",
        description="An item.",
        creator="Tester",
        date="2020-01-01",
        year="2020",
        licenseurl="",
        rights="",
        item_size=1234,
        subjects=["Action"],
        files=[
            file("game.zip", 1000, "ZIP"),
            file("logo_game.png", 20, "PNG"),
            file("screenshot-1.png", 30, "PNG"),
            file("game_meta.xml", 5, "Metadata"),
            file("locked.zip", 10, "ZIP", private=True),
        ],
    )
    base.update(kw)
    return ArchiveItem(**base)


def test_download_urls_quote_the_file_name_not_the_identifier():
    assert build_download_url("demo", "my game (1).zip") == (
        "https://archive.org/download/demo/my%20game%20%281%29.zip"
    )
    assert build_download_url("demo", "a/b.zip") == (
        "https://archive.org/download/demo/a/b.zip"
    )
    with pytest.raises(ValueError):
        build_download_url("../escape", "x.zip")


def test_item_to_dict_and_back_keeps_files_and_private_flags():
    original = item()
    copy = _item_from_cache(_item_to_dict(original))
    assert copy.identifier == original.identifier
    assert copy.title == original.title
    assert copy.subjects == original.subjects
    assert [f.name for f in copy.files] == [f.name for f in original.files]
    private = next(f for f in copy.files if f.name == "locked.zip")
    assert private.private is True
    assert private.downloadable is False


def test_cache_parsing_skips_unusable_files():
    raw = {
        "identifier": "demo",
        "files": [
            {"name": ""},
            "not a mapping",
            {"name": "game.zip", "size": "not a number"},
            {"name": "ok.zip", "size": "12", "sha256": "abc"},
        ],
    }
    parsed = _item_from_cache(raw)
    assert [f.name for f in parsed.files] == ["game.zip", "ok.zip"]
    assert parsed.files[1].size == 12
    assert parsed.files[1].sha256 == "abc"


def test_poster_prefers_a_logo_over_a_screenshot():
    poster = item().poster_file
    assert poster is not None and poster.name == "logo_game.png"
    assert item().poster_url.endswith("/logo_game.png")
    locked = item(files=[file("logo_game.png", 10, "PNG", private=True)])
    assert locked.poster_file is None


def test_screenshots_are_images_and_never_private_files():
    shots = item().screenshot_files
    names = {f.name for f in shots}
    assert names == {"screenshot-1.png"}


def test_primary_files_drop_auxiliary_metadata():
    primary = {f.name for f in item().primary_files}
    assert "game.zip" in primary
    assert "game_meta.xml" not in primary


def test_game_summary_parsing_falls_back_where_it_must():
    game = _game_from_doc({
        "identifier": "demo",
        "title": "",
        "date": "1999-05-01",
        "downloads": "12",
        "subject": "Action",
    })
    assert game is not None
    assert game.title == "demo"
    assert game.year == "1999"
    assert game.downloads == 12
    assert game.subjects == ["Action"]
    assert game.subtitle.startswith("1999")


def test_game_summary_rejects_identifiers_that_could_escape():
    assert _game_from_doc({"identifier": "../escape"}) is None
    assert _game_from_doc({}) is None
