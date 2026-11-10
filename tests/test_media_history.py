"""The Movies & TV playback history: what a play writes, and what old rows read as.

The store is the shared settings document, so a plain dict with ``get``/``set``
is enough here.  No network, no GTK and no Qt: these are the rules the cards on
both desktops depend on.
"""

from __future__ import annotations

from lgl import media_history

POSTER = "https://image.tmdb.org/t/p/w500/matrix.jpg"


class Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set(self, key, value):
        self[key] = value


def seed(*records):
    store = Store()
    store[media_history.HISTORY_KEY] = list(records)
    return store


# -- what is written ---------------------------------------------------------
def test_a_play_persists_the_poster_url():
    store = Store()
    media_history.record(
        store, key="movie:603", title="The Matrix", poster_url=POSTER, year="1999"
    )
    entry = media_history.load(store)[0]
    assert entry["poster_url"] == POSTER
    assert entry["title"] == "The Matrix"


def test_playing_again_without_a_poster_keeps_the_stored_artwork():
    store = seed(
        {
            "key": "tv:1399",
            "title": "Game of Thrones",
            "poster_url": POSTER,
            "year": "2011",
            "watched_at": 1.0,
        }
    )
    media_history.record(store, key="tv:1399", title="Game of Thrones", year="2011")
    entry = media_history.load(store)[0]
    assert entry["poster_url"] == POSTER
    # Re-watching still moves the row to the front instead of duplicating it.
    assert len(media_history.load(store)) == 1


def test_a_value_that_is_not_a_url_is_stored_empty():
    store = Store()
    media_history.record(
        store, key="movie:603", title="The Matrix", poster_url="/w500/matrix.jpg"
    )
    assert media_history.load(store)[0]["poster_url"] == ""


def test_an_http_poster_is_kept():
    store = Store()
    media_history.record(
        store, key="movie:603", title="The Matrix", poster_url="http://example.test/a.jpg"
    )
    assert media_history.load(store)[0]["poster_url"] == "http://example.test/a.jpg"


# -- what old rows read as ---------------------------------------------------
def test_an_older_record_without_a_poster_stays_readable():
    store = seed({"key": "tv:1399", "title": "Game of Thrones", "watched_at": 1.0})
    entry = media_history.load(store)[0]
    assert entry["title"] == "Game of Thrones"
    assert entry["poster_url"] == ""


def test_an_older_record_that_called_the_field_poster_still_draws_it():
    store = seed({"key": "movie:603", "title": "The Matrix", "poster": POSTER,
                  "watched_at": 1.0})
    entry = media_history.load(store)[0]
    assert entry["poster_url"] == POSTER


def test_recording_a_new_title_leaves_older_rows_intact():
    store = seed(
        {"key": "tv:1399", "title": "Game of Thrones", "watched_at": 1.0},
        {"key": "movie:603", "title": "The Matrix", "poster": POSTER, "watched_at": 2.0},
    )
    media_history.record(store, key="movie:550", title="Fight Club", poster_url=POSTER)
    titles = [entry["title"] for entry in media_history.load(store)]
    assert titles == ["Fight Club", "The Matrix", "Game of Thrones"]
    older = {entry["key"]: entry for entry in media_history.load(store)}
    assert older["movie:603"]["poster_url"] == POSTER
    assert older["tv:1399"]["poster_url"] == ""
