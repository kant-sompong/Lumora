"""Row text and button rules for the Qt Downloads panel (no display needed)."""

import pytest

pytest.importorskip("PySide6")

from lgl.archive import ArchiveFile  # noqa: E402
from lgl.downloader import DownloadStatus, TaskSnapshot  # noqa: E402
from lgl.qt_downloads import (  # noqa: E402
    actions_for,
    active_count,
    default_selection,
    detail_text,
    downloadable_files,
)


def snap(status, **kw):
    base = dict(
        id="t1", identifier="game", filename="game.zip", folder="/dl/game",
        final_path="/dl/game/game.zip", part_path="/dl/game/game.zip.part",
        url="https://archive.org/x", downloaded=1024 * 1024, total=4 * 1024 * 1024,
        status=status, speed=0.0, eta=None, error=None, created_at=0.0,
        attempts=0, retry_limit=3,
    )
    base.update(kw)
    return TaskSnapshot(**base)


def test_running_text_includes_speed_and_eta():
    text = detail_text(snap(DownloadStatus.RUNNING, speed=2 * 1024 * 1024, eta=90))
    assert text == "1.0 MiB of 4.0 MiB · 2.0 MiB/s · 1m 30s left"


def test_running_without_speed_shows_only_progress():
    assert detail_text(snap(DownloadStatus.RUNNING)) == "1.0 MiB of 4.0 MiB"


def test_unknown_total_is_spelled_out():
    text = detail_text(snap(DownloadStatus.PAUSED, total=None))
    assert text.startswith("Paused") and "unknown size" in text


def test_waiting_saved_and_cancelled_texts():
    assert detail_text(snap(DownloadStatus.QUEUED)) == "Waiting to start"
    assert detail_text(snap(DownloadStatus.COMPLETED)) == "Saved to /dl/game"
    assert "partial file kept" in detail_text(snap(DownloadStatus.CANCELLED))


def test_failed_text_shows_reason_and_retries_used():
    text = detail_text(snap(DownloadStatus.FAILED, error="HTTP 404", attempts=1))
    assert text == "Failed: HTTP 404 · retry 1 of 3 used"
    assert detail_text(snap(DownloadStatus.FAILED, retry_limit=0)) == "Failed: unknown error"


@pytest.mark.parametrize("status,expected", [
    (DownloadStatus.RUNNING, ["pause", "cancel"]),
    (DownloadStatus.PAUSED, ["resume", "cancel"]),
    (DownloadStatus.QUEUED, ["cancel"]),
    (DownloadStatus.COMPLETED, ["open", "remove"]),
    (DownloadStatus.FAILED, ["retry", "remove"]),
    (DownloadStatus.CANCELLED, ["retry", "remove"]),
])
def test_actions_by_status(status, expected):
    assert actions_for(snap(status)) == expected


def test_no_retry_button_once_the_limit_is_used_up():
    assert actions_for(snap(DownloadStatus.FAILED, attempts=3)) == ["remove"]


def test_active_count_ignores_finished_tasks():
    snaps = [snap(DownloadStatus.RUNNING), snap(DownloadStatus.QUEUED),
             snap(DownloadStatus.PAUSED), snap(DownloadStatus.COMPLETED),
             snap(DownloadStatus.FAILED)]
    assert active_count(snaps) == 3


def test_file_selection_skips_private_and_preselects_game_files():
    game = ArchiveFile(name="game.zip", size=10, format="ZIP")
    meta = ArchiveFile(name="game_meta.xml", size=1, format="Metadata")
    secret = ArchiveFile(name="locked.zip", size=5, format="ZIP", private=True)

    class Item:
        files = [game, meta, secret]

    files = downloadable_files(Item())
    assert files == [game, meta]
    assert default_selection(files) == [game]
