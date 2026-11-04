from datetime import datetime, timezone

import pytest

from lgl import anime_calendar as cal
from lgl.notifications import Deduper

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        (0, None),
        (NOW, NOW),
        ("1762261200", 1762261200.0),
        ("2026-11-06T21:00:00Z", 1793998800.0),
        ("2026-11-06T21:00:00+00:00", 1793998800.0),
        ("2026-11-06T22:00:00+01:00", 1793998800.0),
        ("nonsense", None),
    ],
)
def test_parse_when(value, expected):
    assert cal.parse_when(value) == expected


def test_naive_dates_are_treated_as_utc():
    naive = cal.parse_when("2026-11-06T21:00:00")
    aware = cal.parse_when("2026-11-06T21:00:00Z")
    assert naive == aware


def test_entry_from_rejects_titleless_rows():
    assert cal.entry_from({"air_at": NOW}) is None
    entry = cal.entry_from({"title": "Frieren", "episode": 5, "air_at": NOW})
    assert entry.key == "frieren" and entry.episode == "5"


def test_upcoming_orders_dated_entries_and_keeps_unknown_last():
    items = [
        {"title": "Later", "air_at": NOW + 3 * DAY},
        {"title": "Sooner", "air_at": NOW + 1 * DAY},
        {"title": "TBA"},
        {"title": "Past", "air_at": NOW - 2 * DAY},
        {"title": "Too far", "air_at": NOW + 30 * DAY},
    ]
    got = cal.upcoming(items, now=NOW, days=7)
    assert [e.title for e in got] == ["Sooner", "Later", "TBA"]


def test_upcoming_respects_follows_and_limit():
    items = [
        {"title": "A", "key": "a", "air_at": NOW + DAY},
        {"title": "B", "key": "b", "air_at": NOW + DAY},
    ]
    assert [e.title for e in cal.upcoming(items, now=NOW, follows_only=["b"])] == ["B"]
    assert len(cal.upcoming(items, now=NOW, limit=1)) == 1


def test_follows_roundtrip_and_deduplication():
    settings = Store()
    assert cal.follows(settings) == []
    assert cal.follow(settings, "frieren") is True
    assert cal.follow(settings, "frieren") is True
    assert cal.follows(settings) == ["frieren"]
    assert cal.is_following(settings, "frieren") is True
    assert cal.unfollow(settings, "frieren") is True
    assert cal.unfollow(settings, "frieren") is False
    assert cal.follow(settings, "") is False


def test_follows_are_capped():
    settings = Store()
    for i in range(cal.MAX_FOLLOWS):
        assert cal.follow(settings, f"show-{i}") is True
    assert cal.follow(settings, "one-too-many") is False


def test_timezone_aware_formatting():
    entry = cal.entry_from({"title": "Show", "episode": 1, "air_at": "2026-11-06T21:00:00Z"})
    utc = cal.format_when(entry, "UTC")
    tokyo = cal.format_when(entry, "Asia/Tokyo")
    assert "21:00" in utc and "UTC" in utc
    assert "06:00" in tokyo or "Sat" in tokyo
    # An unknown zone falls back to UTC rather than raising.
    assert cal.format_when(entry, "Mars/Olympus") == utc
    assert cal.format_when(cal.entry_from({"title": "TBA"}), "UTC") == cal.UNKNOWN_LABEL


def test_describe_and_grouped_headings():
    entry = cal.entry_from({"title": "Frieren", "episode": 3, "air_at": NOW, "delayed": True})
    text = cal.describe(entry, "UTC")
    assert "Frieren" in text and "episode 3" in text and "delayed" in text
    groups = cal.grouped([entry, cal.entry_from({"title": "TBA"})], "UTC")
    labels = [label for label, _ in groups]
    assert any("November" in label for label in labels)
    assert cal.UNKNOWN_LABEL in labels


def test_notify_due_only_for_imminent_episodes_once():
    sent = []
    deduper = Deduper(window=3600)
    entry = cal.entry_from({"title": "Frieren", "episode": 7, "key": "f", "air_at": NOW + 3600})
    far = cal.entry_from({"title": "Other", "key": "o", "air_at": NOW + 5 * DAY})
    count = cal.notify_due([entry, far], now=NOW, notify=lambda t, m: sent.append(t), deduper=deduper)
    assert count == 1 and sent == ["Frieren episode 7"]
    again = cal.notify_due([entry], now=NOW, notify=lambda t, m: sent.append(t), deduper=deduper)
    assert again == 0
    assert cal.notify_due([entry], now=NOW, notify=None) == 0
