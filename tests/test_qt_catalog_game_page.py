"""The Qt catalogue game page: facts, link rows, magnets and per-link actions.

Everything here is offscreen and offline: ``kryoto.check_link`` and the browser
opener are stubbed, so a test never touches the network.  The point is the same
one the Linux detail panel makes: a recorded link is not a downloadable file
until a check says so, and a web page is never queued.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lgl import kryoto, platform_support, qt_catalog_links  # noqa: E402
from lgl.catalog_store import CatalogStore, MagnetRecord, MirrorRecord  # noqa: E402
from lgl.qt_catalog_links import CatalogGamePane  # noqa: E402
from lgl.sources import SourceGame  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _drain_workers():
    """Let a stubbed check finish before the suite's worker guard runs."""
    yield
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)


def wait_for(app: QApplication, predicate, timeout: float = 5.0) -> bool:
    """Run the event loop until a background worker lands, or give up."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Recorder:
    """A download manager that only remembers what it was asked to queue."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add(self, **kwargs) -> None:
        self.calls.append(kwargs)


class Torrents:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []

    def add(self, uri: str, title: str = "") -> None:
        self.added.append((uri, title))


def _store(tmp_path: Path, *, links: bool = True) -> CatalogStore:
    store = CatalogStore(tmp_path / "catalog.json")
    store.upsert_game(
        SourceGame(
            source_id="kryoto",
            game_id="w-w-d-d",
            title="W.W.D.D.",
            year="2024",
            developer="Studio Nine",
            publisher="Studio Nine",
            genres=("Adventure",),
            platforms=("Windows",),
            description="A short, honest description.",
            source_url="https://kryo.to/games/w-w-d-d",
            extra={"source_fields": {"size": "1.2 GB"}, "version": "1.0"},
        )
    )
    if links:
        store.add_mirror(
            MirrorRecord(
                source_id="kryoto", game_key="kryoto:w-w-d-d",
                url="https://kryo.to/dl/WWDD-setup.zip",
                filename="WWDD-setup.zip", size=4096,
                host="kryo.to", url_kind="file-candidate", label="Main mirror",
            )
        )
        store.add_mirror(
            MirrorRecord(
                source_id="kryoto", game_key="kryoto:w-w-d-d",
                url="https://mirror.example/WWDD.zip",
                filename="WWDD.zip", host="mirror.example",
                url_kind="file-candidate",
            )
        )
        store.add_magnet(
            MagnetRecord(
                infohash="a" * 40,
                uri="magnet:?xt=urn:btih:" + "a" * 40 + "&dn=W.W.D.D.",
                display_name="W.W.D.D.",
                origin="extracted",
                refs=[{"source_id": "kryoto", "game_key": "kryoto:w-w-d-d"}],
            )
        )
    store.save()
    return store


def _pane(app, tmp_path, *, links: bool = True):
    store = _store(tmp_path, links=links)
    manager, torrents = Recorder(), Torrents()
    messages: list[tuple[str, bool]] = []
    pane = CatalogGamePane(
        store, manager, torrents, tmp_path,
        on_message=lambda text, error=False: messages.append((text, error)),
    )
    pane.resize(420, 900)
    pane.show()
    app.processEvents()
    return pane, store, manager, torrents, messages


def _texts(widget) -> list[str]:
    return [child.text() for child in widget.findChildren(QLabel) if child.text()]


# --------------------------------------------------------------------------
# the page itself
# --------------------------------------------------------------------------


def test_the_page_shows_two_links_and_one_magnet(app, tmp_path):
    pane, store, _manager, _torrents, _messages = _pane(app, tmp_path)

    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")

    assert len(pane.links.rows) == 2
    assert len(pane.magnets.rows) == 1
    shown = " ".join(_texts(pane))
    assert "https://kryo.to/dl/WWDD-setup.zip" in shown
    assert "https://mirror.example/WWDD.zip" in shown
    assert "Main mirror" in shown
    assert "Reported by the source" in shown
    # Facts come from the stored record, not from a request.
    assert pane._facts["Developer"].text() == "Studio Nine"
    assert pane._facts["Genres"].text() == "Adventure"
    assert pane._facts["Size"].text() == "install 1.2 GB"
    assert pane._facts["Identifier"].text() == "w-w-d-d"
    assert pane.description.text() == "A short, honest description."
    assert pane.jd_btn.isEnabled() is True
    pane.close()


def test_a_record_without_links_says_so(app, tmp_path):
    pane, store, _manager, _torrents, _messages = _pane(app, tmp_path, links=False)

    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")

    shown = " ".join(_texts(pane))
    assert "No source links were reported for this game" in shown
    assert "did not expose a magnet link" in shown
    assert pane.jd_btn.isEnabled() is False
    assert pane.ab_btn.isEnabled() is False
    pane.close()


def test_copy_and_open_work_on_a_link_row(app, tmp_path, monkeypatch):
    pane, store, _manager, _torrents, _messages = _pane(app, tmp_path)
    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")
    row = pane.links.rows[0]
    opened: list[str] = []
    monkeypatch.setattr(
        platform_support, "open_url", lambda url: opened.append(url) or True
    )
    QApplication.clipboard().clear()

    row._copy()
    row._open()

    assert QApplication.clipboard().text() == "https://kryo.to/dl/WWDD-setup.zip"
    assert opened == ["https://kryo.to/dl/WWDD-setup.zip"]
    pane.close()


def test_the_magnet_row_uses_the_torrent_queue(app, tmp_path):
    pane, store, _manager, torrents, _messages = _pane(app, tmp_path)
    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")

    pane.magnets._start(store.magnets_for("kryoto:w-w-d-d")[0], "W.W.D.D.")

    assert torrents.added and torrents.added[0][0].startswith("magnet:?xt=urn:btih:")
    pane.close()


# --------------------------------------------------------------------------
# per-link actions
# --------------------------------------------------------------------------


def test_check_persists_the_answer_and_redraws_the_row(app, tmp_path, monkeypatch):
    pane, store, _manager, _torrents, messages = _pane(app, tmp_path)
    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")
    monkeypatch.setattr(
        qt_catalog_links.kryoto, "check_link",
        lambda url, allow_external=True: kryoto.LinkCheck(
            url=url, status="verified", kind="file",
            content_type="application/zip", filename="WWDD-setup.zip", size=4096,
        ),
    )

    pane.links.rows[0]._check()

    assert wait_for(
        app, lambda: pane.links.rows[0].record.check_status == "verified"
    )
    assert wait_for(app, lambda: any("Check:" in text for text, _ in messages))
    stored = CatalogStore(store.path).mirrors_for("kryoto:w-w-d-d")
    first = next(link for link in stored if link.url.endswith("WWDD-setup.zip"))
    assert first.check_status == "verified"
    assert first.content_type == "application/zip"
    assert "verified by a request" in pane.links.rows[0].detail.text()
    pane.close()


def test_download_on_a_page_link_queues_nothing(app, tmp_path, monkeypatch):
    pane, store, manager, _torrents, messages = _pane(app, tmp_path)
    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")
    monkeypatch.setattr(
        qt_catalog_links.kryoto, "check_link",
        lambda url, allow_external=True: kryoto.LinkCheck(
            url=url, status="page", kind="page", content_type="text/html",
        ),
    )

    pane.links.rows[0]._download()

    assert wait_for(app, lambda: any("web page" in text for text, _ in messages))
    assert manager.calls == []
    stored = CatalogStore(store.path).mirrors_for("kryoto:w-w-d-d")
    page_link = next(link for link in stored if link.url.endswith("WWDD-setup.zip"))
    assert page_link.check_status == "page"
    pane.close()


def test_download_on_a_verified_file_asks_first_and_queues(app, tmp_path, monkeypatch):
    pane, store, manager, _torrents, messages = _pane(app, tmp_path)
    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")
    verified = pane.links.rows[0].record
    verified.check_status = "verified"
    verified.url_kind = "file"
    asked: list[str] = []
    monkeypatch.setattr(
        qt_catalog_links, "confirm_download",
        lambda parent, record, folder: asked.append(record.filename) or True,
    )

    pane.links.rows[0]._download()

    assert asked == ["WWDD-setup.zip"]
    assert len(manager.calls) == 1
    assert manager.calls[0]["filename"] == "WWDD-setup.zip"
    assert manager.calls[0]["url"] == "https://kryo.to/dl/WWDD-setup.zip"
    assert Path(manager.calls[0]["download_dir"]) == tmp_path
    assert any(text.startswith("Queued ") for text, _error in messages)
    pane.close()


def test_a_refused_confirmation_queues_nothing(app, tmp_path, monkeypatch):
    pane, store, manager, _torrents, _messages = _pane(app, tmp_path)
    pane.show_record(store.get_game("kryoto:w-w-d-d"), "Kryoto")
    verified = pane.links.rows[0].record
    verified.check_status = "verified"
    verified.url_kind = "file"
    monkeypatch.setattr(
        qt_catalog_links, "confirm_download", lambda parent, record, folder: False
    )

    pane.links.rows[0]._download()

    assert manager.calls == []
    pane.close()


# --------------------------------------------------------------------------
# the Browse window uses the same page
# --------------------------------------------------------------------------


def test_browse_shows_the_catalogue_page_for_an_imported_game(app, tmp_path):
    """The placeholder is gone: Browse builds the same page Linux does."""
    from lgl.archive import GameSummary
    from lgl.qt_browse import BrowseDetail

    store = _store(tmp_path)
    detail = BrowseDetail()
    detail.resize(420, 800)
    detail.show()
    record = store.get_game("kryoto:w-w-d-d")
    game = GameSummary(
        identifier="catalog:kryoto:w-w-d-d", title="W.W.D.D.", year="2024",
        downloads=0, item_size=None,
        catalog_key="kryoto:w-w-d-d", catalog_source="kryoto",
    )

    detail.show_game(game)
    detail.show_catalog(record, "kryoto", {
        "store": store, "manager": Recorder(), "torrents": Torrents(),
        "download_dir": tmp_path,
        "on_message": lambda text, error=False: None,
    })
    app.processEvents()

    assert detail.catalog_host.isVisible() is True
    assert detail.catalog_pane is not None
    assert len(detail.catalog_pane.links.rows) == 2
    shown = " ".join(_texts(detail))
    assert "https://kryo.to/dl/WWDD-setup.zip" in shown
    assert "Imported catalogue record" not in shown
    detail.close()
