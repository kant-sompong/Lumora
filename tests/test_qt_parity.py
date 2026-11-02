"""Tests for the Windows parity work: downloads, catalogue and artwork.

These run headless like the rest of the Qt suite.  Anything that touches the
network is exercised against a local HTTP server, and the pure helpers are
tested directly so a broken rule fails here rather than in a build.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtGui = pytest.importorskip("PySide6.QtGui")

from lgl.catalog_store import CatalogGame, CatalogStore  # noqa: E402
from lgl.downloader import DownloadStatus  # noqa: E402
from lgl.qt_catalog import (  # noqa: E402
    CatalogDownloadDialog,
    catalog_identifier,
    mirror_filename,
)


@pytest.fixture(scope="module")
def app():
    instance = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return instance


# --------------------------------------------------------------------------
# downloads: notices and leftovers
# --------------------------------------------------------------------------


def _snap(status: DownloadStatus, task_id: str = "t1", **extra) -> SimpleNamespace:
    data = {
        "id": task_id,
        "status": status,
        "filename": "/downloads/game.zip",
        "identifier": "game",
        "error": "",
        "can_retry": True,
        "attempts": 0,
        "retry_limit": 3,
    }
    data.update(extra)
    return SimpleNamespace(**data)


def test_only_a_fresh_finish_notifies():
    from lgl.qt_downloads import should_notify

    done = _snap(DownloadStatus.COMPLETED)
    failed = _snap(DownloadStatus.FAILED, error="connection reset")

    assert should_notify(DownloadStatus.RUNNING, done)
    assert should_notify(DownloadStatus.PAUSED, failed)
    # Already reported, or never seen running: do not repeat the notification.
    assert not should_notify(DownloadStatus.COMPLETED, done)
    assert not should_notify(None, done)
    # Cancelling is a choice, not something to announce.
    assert not should_notify(DownloadStatus.QUEUED, _snap(DownloadStatus.CANCELLED))


def test_unfinished_count_covers_leftovers_only():
    from lgl.qt_downloads import unfinished_count

    snaps = [
        _snap(DownloadStatus.PAUSED, "a"),
        _snap(DownloadStatus.QUEUED, "b"),
        _snap(DownloadStatus.FAILED, "c"),
        _snap(DownloadStatus.CANCELLED, "d"),
        _snap(DownloadStatus.RUNNING, "e"),
        _snap(DownloadStatus.COMPLETED, "f"),
    ]
    assert unfinished_count(snaps) == 4
    assert unfinished_count([]) == 0


def test_resume_leftovers_restarts_paused_and_retryable_only():
    from lgl.qt_downloads import resume_leftovers

    class Manager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self._snaps = [
                _snap(DownloadStatus.PAUSED, "paused"),
                _snap(DownloadStatus.FAILED, "failed", can_retry=True),
                _snap(DownloadStatus.COMPLETED, "done"),
                _snap(DownloadStatus.FAILED, "hopeless", can_retry=False),
            ]

        def snapshots(self):
            return self._snaps

        def resume(self, task_id):
            self.calls.append(("resume", task_id))

        def retry(self, task_id):
            self.calls.append(("retry", task_id))

    manager = Manager()
    assert resume_leftovers(manager) == 2
    assert manager.calls == [("resume", "paused"), ("retry", "failed")]


def test_unknown_external_manager_is_refused():
    from lgl.qt_downloads import send_to_external

    with pytest.raises(ValueError):
        send_to_external("not-a-manager", ["https://example.org/a.zip"])


# --------------------------------------------------------------------------
# browse: screenshots, mirror checks and the catalogue hand-off
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):  # noqa: N802 - http.server API
        self.send_response(200 if self.path == "/ok.bin" else 404)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def log_message(self, *args):  # silence the test server
        return


@pytest.fixture()
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


def test_probe_urls_reports_what_answered(http_server):
    from lgl.qt_browse import probe_urls

    rows = probe_urls(
        [f"{http_server}/ok.bin", f"{http_server}/missing.bin", "file:///etc/passwd"],
        timeout=5,
    )
    assert len(rows) == 2  # the file:// link is not probed
    assert rows[0][1] is True
    assert rows[1][1] is False
    assert "404" in rows[1][2]


def test_screenshot_viewer_steps_through_its_images(app):
    from lgl.qt_browse import ScreenshotViewer

    shots = [QtGui.QPixmap(20, 10), QtGui.QPixmap(30, 15)]
    for pix in shots:
        pix.fill(QtGui.QColor("#123456"))
    viewer = ScreenshotViewer(shots, 0)
    assert viewer.counter.text() == "1 of 2"
    viewer._step(1)
    assert viewer.counter.text() == "2 of 2"
    viewer._step(1)  # wraps
    assert viewer.counter.text() == "1 of 2"
    viewer.deleteLater()


def test_browse_detail_offers_files_copy_and_external_managers(app):
    from lgl.archive import ArchiveItem
    from lgl.qt_browse import BrowseDetail

    detail = BrowseDetail()
    assert hasattr(detail, "files_requested")
    assert hasattr(detail, "external_requested")
    assert hasattr(detail, "catalog_requested")

    item = ArchiveItem(
        identifier="demo_game",
        title="Demo Game",
        description="A test item.",
        creator="Tester",
        date="2020-01-01",
        year="2020",
        licenseurl="",
        rights="",
        item_size=1024,
    )
    detail.show_item(item)
    assert detail.files_btn.isVisibleTo(detail) is False  # no files to pick
    assert detail.jd_btn.isVisibleTo(detail) is True
    assert detail.ab_btn.isVisibleTo(detail) is True
    detail.deleteLater()


# --------------------------------------------------------------------------
# catalogue downloads and artwork
# --------------------------------------------------------------------------


def test_catalog_helpers_sanitise_keys_and_urls():
    assert catalog_identifier("kryoto:Some Game (2020)") == "kryoto-Some-Game-2020"
    assert catalog_identifier("") == "catalogue-game"
    assert mirror_filename("https://host/files/My%20Game.zip?token=1") == "My Game.zip"
    assert mirror_filename("") == "download"


def test_catalog_download_dialog_explains_an_empty_catalogue(app, tmp_path):
    dialog = CatalogDownloadDialog(
        None, CatalogStore(), None, None, tmp_path, on_queued=None
    )
    assert "carries a download link" in dialog.status.text()
    assert dialog.queue_btn.isEnabled() is False
    dialog.deleteLater()


def test_match_artwork_dialog_builds(app):
    from lgl.qt_artwork import MatchArtworkDialog

    game = CatalogGame(source_id="steam", game_id="42", title="Some Game", year="2019")
    dialog = MatchArtworkDialog(CatalogStore(), game)
    assert dialog.windowTitle() == "Match artwork"
    assert dialog.use_btn.isEnabled() is False
    dialog.deleteLater()


def test_artwork_sync_reports_an_empty_catalogue():
    from lgl.qt_artwork import sync_catalog_artwork

    summary = sync_catalog_artwork(CatalogStore(), None)
    assert summary["total"] == 0
    assert summary["added"] == 0


def test_library_exposes_the_magnet_action_and_media_the_lookup(app):
    from lgl.qt_library import LibraryPage
    from lgl.qt_media import MediaPage

    assert hasattr(LibraryPage, "magnet_requested")
    assert hasattr(LibraryPage, "external_requested")
    assert hasattr(MediaPage, "lookup_anime")


def test_update_check_and_installer_are_wired(app):
    from lgl import updates
    from lgl.qt_main import Window

    assert hasattr(Window, "check_updates")
    status = updates.interpret_release({"tag_name": "v99.0.0"}, "0.5.0")
    assert status.newer is True
    assert updates.interpret_release({"tag_name": "v0.1.0"}, "0.5.0").newer is False
