"""Bulk library actions: the plans, the summaries and the undo, without GTK.

Everything here is plain Python on purpose: the same functions back the GTK bar
now and the Qt screen later, so the rules are tested once.
"""

from __future__ import annotations

from types import SimpleNamespace

from lgl import library_tools
from lgl.collections import CollectionStore


class Settings(dict):
    """The tiny settings surface the tag helpers use."""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        return True


class Store:
    """LibraryStore stand-in that records writes and refuses to delete."""

    def __init__(self, entries=()):
        self._entries = {entry.identifier: entry for entry in entries}
        self.saved = 0

    def entries(self):
        return list(self._entries.values())

    def get(self, identifier):
        return self._entries.get(identifier)

    def set_favorite(self, identifier, value):
        self._entries[identifier].favorite = bool(value)
        return True

    def save(self):
        self.saved += 1
        return True

    def remove(self, identifier):  # pragma: no cover - must never be called
        raise AssertionError("a bulk action must never remove a library entry")


def game(identifier, title, *, year="1998", favorite=False, genres=(), developer=""):
    return SimpleNamespace(
        identifier=identifier,
        title=title,
        year=year,
        favorite=favorite,
        genres=tuple(genres),
        developer=developer,
        publisher="",
        folder=f"/games/{identifier}",
    )


def three():
    return [game("a", "Alpha"), game("b", "Beta"), game("c", "Gamma")]


# -- tags -------------------------------------------------------------------

def test_tag_plan_says_how_many_change_and_what_it_skipped():
    entries = three()
    settings = Settings()
    library_tools.add_tag(settings, "b", "co-op")

    plan = library_tools.plan_tags(
        entries,
        ["a", "b", "c"],
        "Co-op",
        tags_of=lambda entry: library_tools.tags_for(settings, entry),
    )

    assert plan.updated == 2
    assert plan.total == 3
    assert plan.summary() == "2 updated, 1 skipped: already tagged"
    assert plan.confirm() == (
        "Add the tag “co-op” — 2 of 3 games will change; 1 will be left alone."
    )


def test_tag_plan_writes_the_tag_and_undo_takes_it_off_again():
    entries = three()
    settings = Settings()
    library_tools.add_tag(settings, "b", "co-op")
    def tags_of(entry):
        return library_tools.tags_for(settings, entry)

    plan = library_tools.plan_tags(entries, ["a", "b", "c"], "Co-op", tags_of=tags_of)
    assert library_tools.apply_plan(plan, settings=settings) == 2
    assert library_tools.tags_for(settings, "a") == ("co-op",)
    assert library_tools.tags_for(settings, "c") == ("co-op",)

    assert library_tools.apply_plan(plan.undo(), settings=settings) == 2
    assert library_tools.tags_for(settings, "a") == ()
    assert library_tools.tags_for(settings, "c") == ()
    # The game that already had the tag is untouched by the undo.
    assert library_tools.tags_for(settings, "b") == ("co-op",)


def test_removing_a_tag_skips_games_that_never_had_it():
    entries = three()
    settings = Settings()
    for key in ("a", "b"):
        library_tools.add_tag(settings, key, "co-op")
    plan = library_tools.plan_tags(
        entries,
        ["a", "b", "c"],
        "co-op",
        tags_of=lambda entry: library_tools.tags_for(settings, entry),
        remove=True,
    )
    assert plan.updated == 2
    assert plan.summary() == "2 updated, 1 skipped: not tagged"
    assert plan.title == "Remove the tag “co-op”"


def test_an_empty_tag_or_selection_plans_nothing():
    entries = three()
    empty = library_tools.plan_tags(entries, [], "co-op", tags_of=lambda entry: ())
    assert empty.changes == [] and empty.skipped == []
    assert empty.summary() == "0 updated"

    blank = library_tools.plan_tags(entries, ["a"], "   ", tags_of=lambda entry: ())
    assert blank.updated == 0
    assert blank.summary() == "0 updated, 1 skipped: no tag given"


# -- collections ------------------------------------------------------------

def test_collection_plan_skips_members_and_undo_removes_only_what_it_added():
    entries = three()
    store = CollectionStore()
    collection = store.create("Co-op")
    store.add(collection.id, ["a"])
    members = set(store.members(collection.id))

    plan = library_tools.plan_collection(
        entries,
        ["a", "b", "c"],
        "Co-op",
        collection_id=collection.id,
        is_member=lambda entry: entry.identifier in members,
    )

    assert plan.updated == 2
    assert plan.summary() == "2 updated, 1 skipped: already in this collection"
    assert plan.confirm() == (
        "Add to the collection “Co-op” — 2 of 3 games will change; "
        "1 will be left alone."
    )

    library_tools.apply_plan(plan, collections_store=store)
    assert store.members(collection.id) == ["a", "b", "c"]

    library_tools.apply_plan(plan.undo(), collections_store=store)
    assert store.members(collection.id) == ["a"]


# -- favorites --------------------------------------------------------------

def test_favorite_plan_marks_only_the_games_that_are_not_favorites_yet():
    entries = [game("a", "Alpha"), game("b", "Beta", favorite=True), game("c", "Gamma")]
    store = Store(entries)

    plan = library_tools.plan_favorite(entries, ["a", "b", "c"], True)

    assert plan.updated == 2
    assert plan.summary() == "2 updated, 1 skipped: already a favorite"
    assert plan.title == "Mark as favorite"
    assert library_tools.apply_plan(plan, store=store) == 2
    assert [entry.favorite for entry in entries] == [True, True, True]

    library_tools.apply_plan(plan.undo(), store=store)
    assert [entry.favorite for entry in entries] == [False, True, False]


def test_unfavorite_skips_games_that_are_not_favorites():
    entries = [game("a", "Alpha"), game("b", "Beta", favorite=True)]

    plan = library_tools.plan_favorite(entries, ["a", "b"], False)

    assert plan.updated == 1
    assert plan.summary() == "1 updated, 1 skipped: not a favorite"
    assert plan.title == "Unfavorite"


# -- metadata fields --------------------------------------------------------

def test_a_year_that_is_not_a_number_changes_nothing():
    entries = three()
    store = Store(entries)

    plan = library_tools.plan_field(entries, ["a", "b", "c"], "year", "nineteen")

    assert plan.updated == 0
    assert plan.summary() == "0 updated, 3 skipped: year is not a number"
    assert library_tools.apply_plan(plan, store=store) == 0
    assert store.saved == 0
    assert [entry.year for entry in entries] == ["1998", "1998", "1998"]


def test_year_edit_sets_every_selected_game_and_undo_restores_each_old_value():
    entries = [game("a", "Alpha", year="1998"), game("b", "Beta", year="2001")]
    store = Store(entries)

    plan = library_tools.plan_field(entries, ["a", "b"], "year", "1999")
    assert plan.changes == [("a", "1998", "1999"), ("b", "2001", "1999")]
    assert plan.confirm() == "Set Year to “1999” — 2 of 2 games will change."

    library_tools.apply_plan(plan, store=store)
    assert [entry.year for entry in entries] == ["1999", "1999"]

    library_tools.apply_plan(plan.undo(), store=store)
    assert [entry.year for entry in entries] == ["1998", "2001"]


def test_genre_edit_replaces_the_genres_and_undo_puts_the_old_ones_back():
    entries = [game("a", "Alpha", genres=("RPG",)), game("b", "Beta")]
    store = Store(entries)

    plan = library_tools.plan_field(entries, ["a", "b"], "genre", "co-op, Puzzle")
    assert plan.changes[0] == ("a", ("RPG",), ("co-op", "puzzle"))
    assert plan.changes[1] == ("b", (), ("co-op", "puzzle"))

    library_tools.apply_plan(plan, store=store)
    assert entries[0].genres == ("co-op", "puzzle")

    library_tools.apply_plan(plan.undo(), store=store)
    assert entries[0].genres == ("RPG",)
    assert entries[1].genres == ()


def test_a_field_outside_the_bulk_list_is_refused():
    entries = three()
    plan = library_tools.plan_field(entries, ["a"], "magnet", "magnet:?xt=urn:btih:x")
    assert plan.updated == 0
    assert plan.reasons() == "not a field that can be bulk edited"


def test_developer_edit_is_trimmed_and_collapsed():
    entries = [game("a", "Alpha")]
    plan = library_tools.plan_field(entries, ["a"], "developer", "  Studio   Nine ")
    assert plan.changes == [("a", "", "Studio Nine")]


# -- undo history -----------------------------------------------------------

def test_the_history_keeps_only_the_last_bulk_action():
    entries = [game("a", "Alpha")]
    history = library_tools.BulkHistory()
    assert history.can_undo() is False
    assert history.undo() is None

    first = library_tools.plan_tags(entries, ["a"], "one", tags_of=lambda entry: ())
    second = library_tools.plan_tags(entries, ["a"], "two", tags_of=lambda entry: ())
    history.remember(first)
    history.remember(second)
    assert history.can_undo() is True
    assert history.last is second

    undo = history.undo()
    assert undo is not None
    assert undo.changes == [("a", True, False)]
    assert history.can_undo() is False
    assert history.undo() is None


def test_a_plan_with_no_changes_is_not_remembered():
    entries = three()
    plan = library_tools.plan_field(entries, ["a"], "year", "nope")
    history = library_tools.BulkHistory()
    history.remember(plan)
    assert history.can_undo() is False


# -- safety -----------------------------------------------------------------

def test_bulk_actions_never_remove_entries_or_touch_folders():
    entries = three()
    store = Store(entries)  # remove() raises if a bulk action calls it
    before = [(entry.identifier, entry.folder) for entry in entries]

    plan = library_tools.plan_tags(entries, ["a", "b"], "co-op", tags_of=lambda e: ())
    library_tools.apply_plan(plan, settings=Settings(), store=store)
    library_tools.plan_favorite(entries, ["a"], True)
    library_tools.apply_plan(
        library_tools.plan_favorite(entries, ["a"], True), store=store
    )

    assert [(entry.identifier, entry.folder) for entry in entries] == before
    assert store.entries() == entries
