"""End-to-end tests for the download queue against a local HTTP server.

The manager is the part most likely to lose a user's bytes, so these tests run
real requests, real files on disk and a real worker thread.  Nothing is mocked
except the server's contents.
"""

from __future__ import annotations

import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lgl.downloader import (
    DownloadManager,
    DownloadStatus,
    QueueStore,
    TaskSnapshot,
    _friendly_error,
    _is_retryable,
    default_url_validator,
    host_url_validator,
)
from lgl.util import DownloadError

PAYLOAD = bytes(range(256)) * 1200  # 307,200 bytes, easy to compare


class _Handler(BaseHTTPRequestHandler):
    payload = PAYLOAD

    def log_message(self, *args):  # keep pytest output readable
        return

    def do_HEAD(self):  # noqa: N802 - http.server API
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

    def do_GET(self):  # noqa: N802 - http.server API
        if not self.path.startswith(("/file.bin", "/slow")):
            self.send_error(404)
            return
        if self.path.startswith("/slow"):
            body = self.payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            for start in range(0, len(body), 16384):
                try:
                    self.wfile.write(body[start:start + 16384])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.02)
            return
        start = 0
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            first = header[6:].split("-")[0]
            start = int(first) if first.isdigit() else 0
        body = self.payload[start:]
        if start:
            self.send_response(206)
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}",
            )
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def manager(tmp_path):
    mgr = DownloadManager(validate_urls=False, timeout=10)
    yield mgr
    mgr.shutdown()


def wait_for(mgr, task_id, statuses, timeout=25.0):
    deadline = time.time() + timeout
    snap = None
    while time.time() < deadline:
        snap = mgr.get(task_id)
        if snap is not None and snap.status in statuses:
            return snap
        time.sleep(0.02)
    return snap


# --------------------------------------------------------------------------
# URL rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://archive.org/download/demo/game.zip",
    "https://ia800.us.archive.org/download/demo/game.zip",
])
def test_archive_urls_are_accepted(url):
    assert default_url_validator(url)


@pytest.mark.parametrize("url", [
    "http://archive.org/download/demo/game.zip",
    "https://archive.org/details/demo",
    "https://example.org/download/demo/game.zip",
    "https://archive.org.evil.example/download/demo/game.zip",
])
def test_everything_else_is_refused(url):
    assert not default_url_validator(url)


def test_host_validator_allows_named_subdomains_only():
    assert host_url_validator("https://files.example.org/a.zip", ["example.org"])
    assert host_url_validator("https://example.org/a.zip", ["example.org"])
    assert not host_url_validator("https://badexample.org/a.zip", ["example.org"])
    assert not host_url_validator("https://example.org:8443/a.zip", ["example.org"])
    assert not host_url_validator("http://example.org/a.zip", ["example.org"])
    assert not host_url_validator("https://user:pw@example.org/a.zip", ["example.org"])


# --------------------------------------------------------------------------
# Queue persistence
# --------------------------------------------------------------------------


def test_queue_store_round_trip_and_damaged_file(tmp_path):
    path = tmp_path / "queue.json"
    store = QueueStore(path)
    store.save([{"id": "a", "status": "queued"}])
    assert store.load() == [{"id": "a", "status": "queued"}]
    path.write_text("{not json", encoding="utf-8")
    assert store.load() == []


# --------------------------------------------------------------------------
# A real download
# --------------------------------------------------------------------------


def test_download_completes_and_leaves_no_part_file(manager, server, tmp_path):
    task_id = manager.add(
        identifier="demo",
        filename="game.bin",
        url=f"{server}/file.bin",
        download_dir=tmp_path,
        total_size=len(PAYLOAD),
    )
    snap = wait_for(manager, task_id, {DownloadStatus.COMPLETED, DownloadStatus.FAILED})
    assert snap is not None
    assert snap.status == DownloadStatus.COMPLETED, snap.error
    target = Path(snap.final_path)
    assert target.read_bytes() == PAYLOAD
    assert not Path(snap.part_path).exists()
    assert snap.progress == 1.0


def test_duplicate_names_are_refused_not_overwritten(manager, server, tmp_path):
    first = manager.add(
        identifier="demo", filename="game.bin", url=f"{server}/file.bin",
        download_dir=tmp_path,
    )
    wait_for(manager, first, {DownloadStatus.COMPLETED, DownloadStatus.FAILED})
    with pytest.raises(DownloadError):
        manager.add(
            identifier="demo", filename="game.bin", url=f"{server}/file.bin",
            download_dir=tmp_path,
        )
    # A second copy of the same file under another name is fine.
    second = manager.add(
        identifier="demo2", filename="other.bin", url=f"{server}/file.bin",
        download_dir=tmp_path,
    )
    assert second != first


def test_an_existing_file_is_never_clobbered(manager, server, tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "game.bin").write_bytes(b"old contents")
    with pytest.raises(DownloadError):
        manager.add(
            identifier="demo", filename="game.bin", url=f"{server}/file.bin",
            download_dir=tmp_path, folder_name="demo",
        )
    assert (folder / "game.bin").read_bytes() == b"old contents"


def test_cancelled_download_can_be_retried(tmp_path, server):
    mgr = DownloadManager(validate_urls=False, timeout=10)
    try:
        task_id = mgr.add(
            identifier="slow", filename="slow.bin", url=f"{server}/slow.bin",
            download_dir=tmp_path,
        )
        running = wait_for(mgr, task_id, {DownloadStatus.RUNNING})
        assert running is not None
        # Give it a moment to write some bytes, then cancel.
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = mgr.get(task_id)
            if snap is not None and snap.downloaded > 0:
                break
            time.sleep(0.02)
        mgr.cancel(task_id)
        cancelled = wait_for(mgr, task_id, {DownloadStatus.CANCELLED})
        assert cancelled is not None and cancelled.status == DownloadStatus.CANCELLED
        assert cancelled.can_retry

        mgr.retry(task_id)
        finished = wait_for(mgr, task_id, {
            DownloadStatus.COMPLETED, DownloadStatus.FAILED
        })
        assert finished is not None
        assert finished.status == DownloadStatus.COMPLETED, finished.error
        assert finished.attempts == 1
        assert Path(finished.final_path).read_bytes() == PAYLOAD
    finally:
        mgr.shutdown()


def test_retry_limit_zero_leaves_the_task_failed(server, tmp_path):
    mgr = DownloadManager(validate_urls=False, timeout=5, retry_limit=0)
    try:
        task_id = mgr.add(
            identifier="missing", filename="missing.bin",
            url=f"{server}/not-there.bin", download_dir=tmp_path,
        )
        snap = wait_for(mgr, task_id, {DownloadStatus.FAILED})
        assert snap is not None and snap.status == DownloadStatus.FAILED
        assert not snap.can_retry
        mgr.retry(task_id)
        after = mgr.get(task_id)
        assert after is not None and after.status == DownloadStatus.FAILED
        assert "Retry limit reached" in (after.error or "")
    finally:
        mgr.shutdown()


def test_a_finished_queue_is_restored_from_disk(server, tmp_path):
    queue_path = tmp_path / "queue.json"
    (tmp_path / "downloads").mkdir()
    mgr = DownloadManager(validate_urls=False, timeout=10, queue_path=queue_path)
    try:
        task_id = mgr.add(
            identifier="demo", filename="game.bin", url=f"{server}/file.bin",
            download_dir=tmp_path / "downloads",
        )
        snap = wait_for(mgr, task_id, {DownloadStatus.COMPLETED, DownloadStatus.FAILED})
        assert snap is not None and snap.status == DownloadStatus.COMPLETED
    finally:
        mgr.shutdown()

    restored = DownloadManager(validate_urls=False, queue_path=queue_path)
    try:
        again = restored.get(task_id)
        assert again is not None
        assert again.status == DownloadStatus.COMPLETED
        assert Path(again.final_path).read_bytes() == PAYLOAD
    finally:
        restored.shutdown()


def test_file_names_that_escape_are_folded_back_inside(manager, server, tmp_path):
    task_id = manager.add(
        identifier="demo", filename="../../escape.bin",
        url=f"{server}/file.bin", download_dir=tmp_path,
    )
    snap = wait_for(manager, task_id, {
        DownloadStatus.COMPLETED, DownloadStatus.FAILED
    })
    assert snap is not None
    assert str(tmp_path.resolve()) in snap.final_path
    assert ".." not in snap.final_path


def test_snapshot_flags_are_honest():
    snap = TaskSnapshot(
        id="t", identifier="i", filename="f", folder="/f", final_path="/f/f",
        part_path="/f/f.part", url="https://archive.org/download/i/f",
        downloaded=50, total=100, status=DownloadStatus.FAILED, speed=0.0,
        eta=None, error="boom", created_at=0.0, attempts=0, retry_limit=0,
    )
    assert snap.progress == 0.5
    assert snap.can_retry is False
    assert TaskSnapshot(
        id="t", identifier="i", filename="f", folder="/f", final_path="/f/f",
        part_path="/f/f.part", url="u", downloaded=1, total=None,
        status=DownloadStatus.FAILED, speed=0.0, eta=None, error="boom",
        created_at=0.0, attempts=0, retry_limit=1,
    ).can_retry


def test_error_messages_are_written_for_people():
    assert _friendly_error(urllib.error.HTTPError("u", 404, "nope", {}, None)) == (
        "Download failed (HTTP 404)."
    )
    assert "network connection" in _friendly_error(urllib.error.URLError("down"))
    assert "Permission denied" in _friendly_error(PermissionError("denied"))
    assert _is_retryable(DownloadError("server error 503"))
    assert not _is_retryable(DownloadError("The file was not found."))
