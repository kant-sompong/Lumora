from dataclasses import dataclass, field

from lgl import download_dashboard as dd
from lgl.downloader import DownloadStatus


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


@dataclass
class Task:
    id: str = "t1"
    filename: str = "Heat.1995.mkv"
    identifier: str = "heat"
    status: DownloadStatus = DownloadStatus.RUNNING
    downloaded: int = 5 * 1024 * 1024
    total: int | None = 10 * 1024 * 1024
    speed: float = 1024 * 1024
    eta: float | None = 5
    error: str | None = None
    attempts: int = 0
    retry_limit: int = 3
    url: str = "https://example.invalid/heat.mkv"

    @property
    def progress(self):
        return None if not self.total else self.downloaded / self.total

    @property
    def can_retry(self):
        return self.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED) and self.attempts < self.retry_limit


def test_limit_defaults_and_clamping():
    settings = Store()
    assert dd.limit(settings) == dd.DEFAULT_LIMIT
    assert dd.set_limit(settings, 0) == dd.MIN_LIMIT
    assert dd.set_limit(settings, 99) == dd.MAX_LIMIT
    assert dd.set_limit(settings, "junk") == dd.DEFAULT_LIMIT


def test_can_start_respects_the_limit():
    settings = Store()
    dd.set_limit(settings, 2)
    assert dd.can_start(0, settings) is True
    assert dd.can_start(1, settings) is True
    assert dd.can_start(2, settings) is False
    assert dd.can_start("junk", settings) is True


def test_row_carries_progress_for_active_downloads():
    row = dd.row(Task())
    assert row["group"] == "active"
    assert row["title"] == "Heat.1995.mkv"
    assert "%" in row["detail"] and "iB/s" in row["detail"]
    assert row["status"] == "RUNNING"


def test_failed_row_shows_the_error_and_retry_state():
    failed = Task(status=DownloadStatus.FAILED, error="HTTP 404", attempts=1)
    row = dd.row(failed)
    assert row["group"] == "failed"
    assert "HTTP 404" in row["detail"]
    details = dd.error_details(failed)
    assert details["retryable"] is True
    assert details["attempts"] == 1 and details["retry_limit"] == 3
    exhausted = Task(status=DownloadStatus.FAILED, error="boom", attempts=3)
    assert dd.error_details(exhausted)["retryable"] is False


def test_missing_error_text_is_not_blank():
    details = dd.error_details(Task(status=DownloadStatus.FAILED))
    assert details["error"] == "No error was recorded."


def test_rows_filter_by_group_and_summary_counts():
    tasks = [
        Task(id="a"),
        Task(id="b", status=DownloadStatus.QUEUED, downloaded=0, total=None, speed=0.0, eta=None),
        Task(id="c", status=DownloadStatus.FAILED, error="boom"),
    ]
    assert [row["id"] for row in dd.rows(tasks, group="failed")] == ["c"]
    summary = dd.summary(tasks)
    assert summary["counts"]["active"] == 1
    assert summary["failed"] == 1
    assert "failed" in summary["headline"]


def test_queue_file_and_load_queue_are_defensive():
    assert str(dd.queue_file("/tmp/cfg")).endswith("downloads.json")

    class Good:
        def load(self):
            return [{"id": "1"}, "junk"]

    class Bad:
        def load(self):
            raise OSError("corrupt")

    assert dd.load_queue(Good()) == [{"id": "1"}]
    assert dd.load_queue(Bad()) == []
    assert dd.load_queue(object()) == []
