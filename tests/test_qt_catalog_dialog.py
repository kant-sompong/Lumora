"""The Qt Kryoto catalogue dialog: stats, filters, detail pane and refresh.

The importer is replaced with a fake, so a refresh here never touches the
network; what is under test is the wiring the Linux dialog has: the exact
ImportOptions, the worker thread, the cancel event, and the shared link rows.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_catalog  # noqa: E402
from lgl.catalog_store import CatalogStore, MagnetRecord, MirrorRecord  # noqa: E402
from lgl.importer import ImportOutcome  # noqa: E402
from lgl.qt_catalog import KryotoCatalogDialog  # noqa: E402
from lgl.sources import SourceGame  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app: QApplication, predicate, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _store(tmp_path: Path) -> CatalogStore:
    store = CatalogStore(tmp_path / "catalog.json")
    store.upsert_game(SourceGame(
        source_id="kryoto", game_id="linked", title="Linked Game", year="2024",
        developer="Studio Nine",
    ))
    store.upsert_game(SourceGame(
        source_id="kryoto", game_id="empty", title="Empty Game", year="2021",
    ))
    store.add_mirror(MirrorRecord(
        source_id="kryoto", game_key="kryoto:linked",
        url="https://kryo.to/dl/linked.zip", filename="linked.zip",
        host="kryo.to", url_kind="file-candidate",
    ))
    store.add_magnet(MagnetRecord(
        infohash="b" * 40, uri="magnet:?xt=urn:btih:" + "b" * 40,
        display_name="Linked Game",
        refs=[{"source_id": "kryoto", "game_key": "kryoto:linked"}],
    ))
    store.save()
    return store


def _dialog(app, tmp_path):
    dialog = KryotoCatalogDialog(None, catalog=_store(tmp_path), download_dir=tmp_path)
    dialog.show()
    app.processEvents()
    return dialog


def test_the_dialog_lists_imported_games_and_opens_the_detail_pane(app, tmp_path):
    dialog = _dialog(app, tmp_path)

    assert dialog.list.count() == 2
    dialog.filter_combo.setCurrentIndex(
        [dialog.filter_combo.itemData(i) for i in range(dialog.filter_combo.count())].index("with")
    )
    app.processEvents()
    assert dialog.list.count() == 1
    assert "Linked Game" in dialog.list.item(0).text()

    dialog.list.setCurrentRow(0)
    app.processEvents()
    assert dialog.pane.record is not None
    assert dialog.pane.record.game_id == "linked"
    assert len(dialog.pane.links.rows) == 1
    assert len(dialog.pane.magnets.rows) == 1

    dialog.filter_combo.setCurrentIndex(0)
    dialog.search.setText("empty")
    app.processEvents()
    assert dialog.list.count() == 1
    assert "Empty Game" in dialog.list.item(0).text()
    dialog.close()


def test_the_stats_strip_comes_from_the_store(app, tmp_path):
    dialog = _dialog(app, tmp_path)

    assert dialog.stats["Discovered"].text() == "2"
    assert dialog.stats["With links"].text() == "1"
    assert dialog.stats["No links"].text() == "1"
    assert dialog.stats["Links listed"].text() == "1"
    assert dialog.stats["Coverage"].text() == "partial"
    dialog.close()


class FakeImporter:
    """Records the options and honours the cancel event, without a request."""

    seen: dict = {}
    block = False

    def __init__(self, store, source, *, options=None):
        FakeImporter.seen["options"] = options
        FakeImporter.seen["source"] = source
        self.store = store

    def inspect(self):
        FakeImporter.seen["inspected"] = True
        return object()

    def run(self, *, job=None, cancel=None, progress=None, enrich=None):
        FakeImporter.seen.update(job=job, cancel=cancel, enrich=enrich)
        while FakeImporter.block and cancel is not None and not cancel.is_set():
            time.sleep(0.01)
        return ImportOutcome(status="complete", job_id="j1", message="done")


def test_refresh_uses_the_linux_options_in_a_worker(app, tmp_path, monkeypatch):
    FakeImporter.seen = {}
    FakeImporter.block = False
    monkeypatch.setattr(qt_catalog, "CatalogImporter", FakeImporter)
    dialog = _dialog(app, tmp_path)

    dialog.start_refresh(False)

    assert wait_for(app, lambda: not dialog._busy)
    options = FakeImporter.seen["options"]
    assert options.page_size == 25
    assert options.enrich is True
    assert options.workers == 4
    assert FakeImporter.seen["inspected"] is True
    assert FakeImporter.seen["enrich"] is True
    assert "done" in dialog.status.text()
    assert dialog.refresh_btn.isEnabled() is True
    dialog.close()


def test_closing_the_dialog_cancels_the_worker(app, tmp_path, monkeypatch):
    FakeImporter.seen = {}
    FakeImporter.block = True
    monkeypatch.setattr(qt_catalog, "CatalogImporter", FakeImporter)
    dialog = _dialog(app, tmp_path)

    dialog.start_refresh(False)
    assert wait_for(app, lambda: isinstance(FakeImporter.seen.get("cancel"), threading.Event))
    assert dialog.cancel_btn.isEnabled() is True
    dialog.cancel_refresh()

    assert FakeImporter.seen["cancel"].is_set()
    assert wait_for(app, lambda: not dialog._busy)
    assert dialog.cancel_btn.isEnabled() is False
    dialog.close()


def test_resume_continues_the_saved_job(app, tmp_path, monkeypatch):
    FakeImporter.seen = {}
    FakeImporter.block = False
    monkeypatch.setattr(qt_catalog, "CatalogImporter", FakeImporter)
    store = _store(tmp_path)
    job = store.start_job("kryoto", "https://kryo.to/games")
    job.status = "interrupted"
    job.cursor = "page-2"
    store.save()
    dialog = KryotoCatalogDialog(None, catalog=CatalogStore(store.path), download_dir=tmp_path)
    app.processEvents()

    assert dialog.resume_btn.isEnabled() is True
    dialog.start_refresh(True)

    assert wait_for(app, lambda: not dialog._busy)
    assert FakeImporter.seen["job"] is not None
    dialog.close()
