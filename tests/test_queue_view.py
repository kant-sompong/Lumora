"""Queue buckets and row text."""

from __future__ import annotations

from types import SimpleNamespace

from lgl.downloader import DownloadStatus
from lgl.queue_view import counts, filter_tasks, group_of, progress_text


def task(status, filename="game.zip", downloaded=0, total=None, speed=0.0, eta=None):
    progress = None
    if total:
        progress = downloaded / total
    return SimpleNamespace(
        status=status,
        filename=filename,
        downloaded=downloaded,
        total=total,
        speed=speed,
        eta=eta,
        progress=progress,
    )


def test_groups_map_every_status():
    assert group_of(task(DownloadStatus.RUNNING)) == "active"
    assert group_of(task(DownloadStatus.PAUSED)) == "active"
    assert group_of(task(DownloadStatus.QUEUED)) == "queued"
    assert group_of(task(DownloadStatus.COMPLETED)) == "completed"
    assert group_of(task(DownloadStatus.FAILED)) == "failed"
    assert group_of(task(DownloadStatus.CANCELLED)) == "failed"
    assert group_of(SimpleNamespace(status="weird")) == "queued"


def test_counts_always_reports_every_bucket():
    tally = counts([task(DownloadStatus.RUNNING), task(DownloadStatus.FAILED)])
    assert tally == {"active": 1, "queued": 0, "completed": 0, "failed": 1}


def test_filtering_by_bucket_and_name():
    tasks = [
        task(DownloadStatus.RUNNING, "alpha.zip"),
        task(DownloadStatus.COMPLETED, "alpha-two.zip"),
        task(DownloadStatus.FAILED, "beta.zip"),
    ]
    assert [t.filename for t in filter_tasks(tasks, group="active")] == ["alpha.zip"]
    assert [t.filename for t in filter_tasks(tasks, search="ALPHA")] == [
        "alpha.zip",
        "alpha-two.zip",
    ]
    assert filter_tasks(tasks, group="nonsense") == tasks


def test_progress_text_mentions_percent_speed_and_eta():
    line = progress_text(
        task(DownloadStatus.RUNNING, downloaded=500, total=1000, speed=1_200_000, eta=90)
    )
    assert line.startswith("50 %")
    assert "/s" in line
    assert progress_text(task(DownloadStatus.QUEUED)) == ""
