"""Collections: creating, ordering, smart rules and persistence."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lgl.collections import CollectionStore, clean_filters


def store(tmp_path):
    return CollectionStore(tmp_path / "collections.json")


def test_create_rename_delete(tmp_path):
    collections = store(tmp_path)
    cozy = collections.create("Cozy games")
    assert cozy.id == "cozy-games" and cozy.name == "Cozy games"
    assert collections.create("Cozy games 2").id == "cozy-games-2"
    with pytest.raises(ValueError):
        collections.create("cozy GAMES")
    with pytest.raises(ValueError):
        collections.create("   ")
    assert collections.rename(cozy.id, "Comfort games") is True
    assert collections.rename(cozy.id, "Comfort games") is False
    assert collections.delete(cozy.id) is True
    assert collections.delete(cozy.id) is False


def test_membership_and_manual_order(tmp_path):
    collections = store(tmp_path)
    bucket = collections.create("Play next")
    assert collections.add(bucket.id, ["a", "b", "c", "a"]) == 3
    assert collections.members(bucket.id) == ["a", "b", "c"]
    assert collections.move(bucket.id, "c", 0) is True
    assert collections.members(bucket.id) == ["c", "a", "b"]
    assert collections.move(bucket.id, "c", 0) is False
    assert collections.move(bucket.id, "zzz", 0) is False
    assert collections.remove(bucket.id, ["a"]) == 1
    assert collections.members(bucket.id) == ["c", "b"]
    assert collections.containing("c") == ["Play next"]


def test_resolve_keeps_collection_order(tmp_path):
    collections = store(tmp_path)
    bucket = collections.create("Favourites")
    collections.add(bucket.id, ["b", "a"])
    entries = [SimpleNamespace(identifier="a"), SimpleNamespace(identifier="b")]
    resolved = collections.resolve(bucket.id, entries)
    assert [entry.identifier for entry in resolved] == ["b", "a"]


def test_smart_collection_uses_the_library_filter(tmp_path):
    collections = store(tmp_path)
    smart = collections.create("Short games")
    assert collections.set_filters(smart.id, {"genre": "Puzzle", "nonsense": 1}) is True
    assert collections.get(smart.id).smart is True
    assert collections.get(smart.id).filters == {"genre": "Puzzle"}
    seen = {}

    def filterer(entries, **filters):
        seen.update(filters)
        return list(entries)[:1]

    resolved = collections.resolve(smart.id, ["x", "y"], filterer=filterer)
    assert resolved == ["x"] and seen == {"genre": "Puzzle"}
    assert collections.resolve(smart.id, ["x", "y"]) == []
    assert collections.clear_filters(smart.id) is True
    assert collections.get(smart.id).smart is False


def test_persistence_and_document(tmp_path):
    collections = store(tmp_path)
    bucket = collections.create("Keepers")
    collections.add(bucket.id, ["game-1"])
    reopened = store(tmp_path)
    assert reopened.members(bucket.id) == ["game-1"]
    assert reopened.document()["version"] == 1


def test_clean_filters_drops_junk():
    assert clean_filters({"year_from": "1999", "year_to": "nope", "favorite": None}) == {
        "year_from": 1999
    }
    assert clean_filters(None) == {}
