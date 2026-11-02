"""Importer behaviour: options, summaries and a full run over a fake source."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import lgl.importer as importer_module
from lgl.catalog_store import CatalogStore
from lgl.importer import ImportOptions, ImportOutcome, import_source
from lgl.sources import SourceGame, SourcePage, UnsupportedOperation


class FakeSource:
    """Two pages, one record each, no details or download links."""

    id = "fake"
    name = "Fake catalog"
    canonical_url = "https://example.org/catalog"
    url = canonical_url
    collection = ""
    adapter_version = "1"
    source_kind = "catalog"
    hosts = ("example.org",)
    image_hosts = ()
    site = "example.org"
    response_format = "json"
    pagination_method = "page"
    requires_auth = False
    auth_kind = ""
    download_capability = "none"
    permitted_reason = "tests"
    attribution = ""
    attribution_label = ""

    def __init__(self) -> None:
        self.last_error = ""
        self.pages: list[str | None] = []

    def inspect(self):
        raise AssertionError("inspect must not run when the caller skipped it")

    def page(self, cursor=None, limit=100):
        self.pages.append(cursor)
        if cursor == "2":
            return SourcePage(
                games=[SourceGame(source_id="fake", game_id="2", title="Game Two")],
                next_cursor=None,
            )
        return SourcePage(
            games=[
                SourceGame(
                    source_id="fake", game_id="1", title="Game One", year="1999"
                )
            ],
            next_cursor="2",
        )

    def details(self, game_id):
        raise UnsupportedOperation("no details")

    def magnets(self, game_id):
        raise UnsupportedOperation("no magnets")

    def mirrors(self, game_id):
        raise UnsupportedOperation("no mirrors")


@pytest.fixture()
def fake_import(monkeypatch):
    source = FakeSource()
    monkeypatch.setattr(
        importer_module, "source_for_url", lambda url, **_kw: source
    )
    return source


def test_options_are_clamped_to_sane_bounds():
    options = ImportOptions(
        page_size=0, delay=-5, max_retries=99, workers=99,
        enrich_rate=-1, max_records=10 ** 9,
    ).clamped()
    assert options.page_size == 1
    assert options.delay == 0.0
    assert options.max_retries == 10
    assert options.workers == 8
    assert options.enrich_rate == 0.0
    assert 0 < options.max_records <= 32768


def test_outcome_summary_names_what_happened():
    outcome = ImportOutcome(
        status="complete", job_id="job", games_discovered=5, games_imported=4,
        duplicates=1, skipped=2, magnets_found=3, mirrors_found=1, errors=1,
    )
    text = outcome.summary
    assert text.startswith("complete:")
    assert "4 game(s) imported" in text
    assert "5 seen" in text
    assert "3 magnet(s)" in text
    assert "1 error(s)" in text


def test_import_walks_every_page_and_stores_records(fake_import):
    store = CatalogStore()
    outcome, report = import_source(
        store,
        "https://example.org/catalog",
        options=ImportOptions(page_size=10, delay=0),
        inspect_first=False,
    )
    assert report is None
    assert outcome.status == "complete"
    assert outcome.games_discovered == 2
    assert outcome.games_imported == 2
    assert store.count_games() == 2
    assert fake_import.pages == [None, "2"]
    assert store.get_source("fake") is not None


def test_a_cancel_marks_the_run_interrupted_without_errors(fake_import):
    store = CatalogStore()
    cancel = threading.Event()
    cancel.set()
    outcome, _report = import_source(
        store,
        "https://example.org/catalog",
        options=ImportOptions(delay=0),
        cancel=cancel,
        inspect_first=False,
    )
    assert outcome.status == "interrupted"
    assert outcome.games_discovered == 0
    assert store.count_games() == 0


def test_a_failing_page_is_reported_not_raised(fake_import, monkeypatch):
    def explode(cursor=None, limit=100):
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(fake_import, "page", explode)
    store = CatalogStore()
    outcome, _report = import_source(
        store,
        "https://example.org/catalog",
        options=ImportOptions(delay=0),
        inspect_first=False,
    )
    assert outcome.status in ("failed", "partial")
    assert outcome.errors >= 1


def test_an_unreachable_source_is_refused():
    from lgl.sources import SourceError

    with pytest.raises(SourceError):
        import_source(CatalogStore(), "https://example.org/not-a-catalog")
