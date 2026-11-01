"""Builds the real Qt window offscreen and downloads a file end to end."""

import http.server
import os
import socketserver
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lgl import qt_main  # noqa: E402
from lgl.downloader import DownloadManager, DownloadStatus  # noqa: E402

SIZE = 512 * 1024


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(SIZE))
            self.end_headers()
            self.wfile.write(b"x" * SIZE)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def make_window(tmp_path):
    holder = {}
    manager = DownloadManager(
        observer=lambda: holder["b"].notify() if "b" in holder else None,
        validate_urls=False,
        queue_path=tmp_path / "queue.json",
    )
    window = qt_main.MainWindow(None, None, manager, tmp_path)
    holder["b"] = window.bridge
    return window, manager


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_window_builds_and_switches_views(app, tmp_path, mode):
    qt_main.apply_theme(app, mode)
    window, _ = make_window(tmp_path)
    window.show()
    window.show_view("Archive")
    window.show_view("Library")
    window.close()


def test_downloads_page_toggles(app, tmp_path):
    """Ctrl+D opens the Downloads page and returns to the previous one."""
    qt_main.apply_theme(app, "dark")
    window, _ = make_window(tmp_path)
    window.show()
    window.show_view("Library")
    window.toggle_downloads()
    assert window.downloads.isVisible()
    assert window.stack.currentWidget() is window.downloads
    window.toggle_downloads()
    assert not window.downloads.isVisible()
    assert window.stack.currentWidget() is window.library
    window.close()


def test_download_runs_to_completion_and_row_updates(app, tmp_path, server):
    qt_main.apply_theme(app, "dark")
    window, manager = make_window(tmp_path)
    window.show()
    window.toggle_downloads()
    task_id = manager.add(
        identifier="smoke", filename="smoke.zip", url=server + "/smoke.zip",
        download_dir=tmp_path, total_size=SIZE,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        pump(app, 0.1)
        snap = manager.get(task_id)
        if snap is not None and snap.status.terminal:
            break
    pump(app, 0.4)
    snap = manager.get(task_id)
    assert snap.status == DownloadStatus.COMPLETED, snap
    assert (tmp_path / "smoke" / "smoke.zip").stat().st_size == SIZE or any(
        p.stat().st_size == SIZE for p in tmp_path.rglob("smoke.zip")
    )
    assert task_id in window.downloads._rows
    assert window.downloads._rows[task_id].detail.text().startswith("Saved to")
    window.close()
