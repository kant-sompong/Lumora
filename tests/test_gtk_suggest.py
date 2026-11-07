"""The GTK search suggestions; skipped without GTK 3."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")

try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):  # pragma: no cover - GTK 3 typelib missing
    pytest.skip("GTK 3 is not installed", allow_module_level=True)

from lgl import gtk_suggest


def entry(identifier, title=""):
    return SimpleNamespace(identifier=identifier, title=title)


def card(title):
    return SimpleNamespace(title=title)


def test_corpus_uses_titles_and_identifiers_then_cards():
    titles = gtk_suggest.corpus_titles(
        [entry("a", "Alpha"), entry("b"), entry("c", "   ")],
        [card("A Film"), card("")],
    )
    assert titles == ["Alpha", "b", "A Film"]


def test_corpus_reads_the_window_store_and_loaded_cards():
    window = SimpleNamespace(
        library_store=SimpleNamespace(entries=lambda: [entry("a", "Alpha")]),
        media_view=SimpleNamespace(loaded_cards=lambda: [card("A Film")]),
    )
    assert gtk_suggest.corpus(window) == ["Alpha", "A Film"]


def test_at_most_eight_rows_are_offered():
    titles = [f"Game {index}" for index in range(20)]
    rows = gtk_suggest.model_rows("game", titles)
    assert len(rows) == 8
    assert len(gtk_suggest.model_rows("game", titles, limit=3)) == 3


def test_a_subsequence_match_is_offered():
    rows = gtk_suggest.model_rows("hk", ["Hollow Knight", "Portal"])
    assert rows == ["Hollow Knight"]


def test_an_empty_query_clears_the_model():
    store = Gtk.ListStore(str)
    assert gtk_suggest.update_model(store, "po", ["Portal"]) == ["Portal"]
    assert len(store) == 1
    assert gtk_suggest.update_model(store, "", ["Portal"]) == []
    assert len(store) == 0
    assert gtk_suggest.update_model(store, "   ", ["Portal"]) == []


def test_selected_text_reads_the_row_gtk_hands_over():
    store = Gtk.ListStore(str)
    store.append(["Alpha"])
    second = store.append(["Beta"])
    assert gtk_suggest.selected_text(store, second) == "Beta"


def test_the_match_function_accepts_every_row():
    assert gtk_suggest.match_any(None, "anything", None) is True
    assert gtk_suggest.match_any(None, "", None, None) is True
