import json

import pytest

from lgl import metadata_cache as mc


def test_put_and_get_roundtrip(tmp_path):
    now = [1000.0]
    cache = mc.MetadataCache(tmp_path, ttl=60, clock=lambda: now[0])
    assert cache.get("tmdb", "42") is None
    assert cache.put("tmdb", "42", {"title": "Heat", "year": 1995}) is True
    assert cache.get("tmdb", "42") == {"title": "Heat", "year": 1995}
    assert cache.is_fresh("tmdb", "42") is True


def test_expired_entries_are_not_returned(tmp_path):
    now = [1000.0]
    cache = mc.MetadataCache(tmp_path, ttl=60, clock=lambda: now[0])
    cache.put("tmdb", "42", {"title": "Heat"})
    now[0] += 61
    assert cache.get("tmdb", "42") is None
    assert cache.is_fresh("tmdb", "42") is False


def test_provider_namespaces_do_not_collide(tmp_path):
    cache = mc.MetadataCache(tmp_path, ttl=0)
    cache.put("tmdb", "7", {"title": "TMDB title"})
    cache.put("mal", "7", {"title": "MAL title"})
    assert cache.get("tmdb", "7")["title"] == "TMDB title"
    assert cache.get("mal", "7")["title"] == "MAL title"
    # A file moved between providers must not be reused.
    docs = list(tmp_path.rglob("*.json"))
    doc = json.loads(docs[0].read_text("utf-8"))
    assert doc["provider"] in ("tmdb", "mal") and "payload" in doc


def test_corrupt_or_foreign_documents_are_ignored(tmp_path):
    cache = mc.MetadataCache(tmp_path, ttl=0)
    path = cache._entry_path("tmdb", "9")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", "utf-8")
    assert cache.get("tmdb", "9") is None
    path.write_text(json.dumps({"provider": "other", "id": "9", "fetched": 1, "payload": {}}), "utf-8")
    assert cache.get("tmdb", "9") is None


def test_oversized_payload_is_refused(tmp_path):
    cache = mc.MetadataCache(tmp_path, ttl=0)
    assert cache.put("tmdb", "1", {"blob": "x" * (mc.MAX_ENTRY_BYTES)}) is False
    assert cache.put("tmdb", "1", {"ok": True}) is True


def test_artwork_roundtrip_and_missing(tmp_path):
    cache = mc.MetadataCache(tmp_path, ttl=0)
    url = "https://image.tmdb.org/t/p/w500/abc.JPG?x=1"
    assert cache.stored_artwork(url) is None
    path = cache.store_artwork(url, b"\x89PNG data")
    assert path is not None and path.exists()
    assert path.suffix == ".jpg"
    assert cache.stored_artwork(url) == path
    assert cache.store_artwork(url, b"") is None


def test_overrides_win_over_automatic_match_and_persist(tmp_path):
    path = tmp_path / "matches.json"
    overrides = mc.MatchOverrides(path)
    assert overrides.resolve("game:1", [{"id": "auto"}]) == "auto"
    assert overrides.set("game:1", "rawg", "999", title="Right one") is True
    assert overrides.resolve("game:1", [{"id": "auto"}]) == "999"
    again = mc.MatchOverrides(path)
    assert again.get("game:1").provider == "rawg"
    assert again.remove("game:1") is True
    assert again.remove("game:1") is False
    assert mc.MatchOverrides(path).get("game:1") is None


def test_override_rejects_incomplete_input(tmp_path):
    overrides = mc.MatchOverrides(tmp_path / "m.json")
    assert overrides.set("", "rawg", "1") is False
    assert overrides.set("k", "", "1") is False
    assert overrides.set("k", "rawg", "") is False


def test_error_classification_and_backoff(tmp_path):
    now = [100.0]
    cache = mc.MetadataCache(tmp_path, clock=lambda: now[0])
    assert cache.should_fetch("tmdb") is True
    assert cache.record_error("tmdb", Exception("HTTP 429 too many requests")) == mc.RATE_LIMIT
    assert cache.last_error("tmdb") == mc.RATE_LIMIT
    assert cache.should_fetch("tmdb") is False
    now[0] += mc.retry_delay(mc.RATE_LIMIT, 0) + 1
    assert cache.should_fetch("tmdb") is True


@pytest.mark.parametrize(
    "error,kind",
    [
        (Exception("HTTP 429"), mc.RATE_LIMIT),
        (TimeoutError("timed out"), mc.TIMEOUT),
        (Exception("HTTP 401"), mc.AUTH),
        (Exception("HTTP 404"), mc.NOT_FOUND),
        (Exception("HTTP 503"), mc.SERVER),
        (Exception("something odd"), mc.UNKNOWN),
    ],
)
def test_classify_error(error, kind):
    assert mc.classify_error(error) == kind
    assert mc.user_message(kind)


def test_placeholder_is_deterministic_and_safe():
    assert mc.placeholder_color("Heat") == mc.placeholder_color("Heat")
    assert mc.placeholder_color("Heat") != mc.placeholder_color("Alien")
    svg = mc.placeholder_svg("Heat", "<script>alert(1)</script>")
    assert svg.startswith(b"<svg") and b"<script>" not in svg
    assert b"&lt;" in svg
    assert len(mc.placeholder_svg("x", "", size=10_000)) < 2000


def test_prune_and_stats(tmp_path):
    now = [10.0]
    cache = mc.MetadataCache(tmp_path, ttl=100, clock=lambda: now[0])
    for i in range(5):
        cache.put("tmdb", str(i), {"i": i})
    assert cache.stats()["files"] == 5
    now[0] += 101
    assert cache.prune() == 5
    assert cache.stats()["files"] == 0


def test_clear_removes_everything(tmp_path):
    cache = mc.MetadataCache(tmp_path, ttl=0)
    cache.put("tmdb", "1", {"a": 1})
    cache.store_artwork("https://x/y.png", b"data")
    assert cache.clear() >= 2
    assert cache.stats()["files"] == 0
