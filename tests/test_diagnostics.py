"""Support reports must never carry credentials or personal paths."""

from __future__ import annotations

from lgl.diagnostics import build_report, redact, write_report
from lgl.settings import Settings


def test_redact_shapes_and_labels():
    assert "<redacted>" in redact("token: abcdefghijklmnopqrstuvwxyz012345")
    assert "SECRETVALUE" not in redact("api_key=SECRETVALUE")
    assert "/home/alice" not in redact("/home/alice/games/library.json")


def test_report_lists_providers_without_values(tmp_path):
    settings = Settings(tmp_path / "settings.json", keyring=False)
    settings.set("api_keys", {"tmdb": "SUPERSECRETKEY123"})
    settings.set("download_dir", "/home/alice/downloads")
    report = build_report(
        app_version="0.6.4",
        settings=settings,
        library_entries=12,
        collections=3,
        continue_watching=2,
        extra={"last error": "HTTP 500 from api_key=SUPERSECRETKEY123"},
        now=0,
    )
    assert "app version: 0.6.4" in report
    assert "provider tmdb: configured" in report
    assert "provider mal: not configured" in report
    assert "SUPERSECRETKEY123" not in report
    assert "/home/alice" not in report
    assert "library entries: 12" in report


def test_log_tail_is_redacted_and_written(tmp_path):
    log = tmp_path / "lumora.log"
    log.write_text(
        "starting\n"
        "GET https://api.example/x?token=abcdefghijklmnopqrstuvwx\n"
        "saved to /home/bob/games\n",
        encoding="utf-8",
    )
    target = write_report(
        tmp_path / "report.txt", app_version="0.6.4", settings=None, log_paths=[log]
    )
    text = target.read_text(encoding="utf-8")
    assert "lumora.log" in text
    assert "abcdefghijklmnopqrstuvwx" not in text
    assert "/home/bob" not in text
    assert "settings: unavailable" in text
