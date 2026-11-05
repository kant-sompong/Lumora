"""Recently viewed history: order, de-duplication, refusal and recovery."""

from lgl import recently_viewed as rv


def _record(path, kind="game", key="a", title="Alpha", **kwargs):
    return rv.record(path, kind=kind, key=key, title=title, **kwargs)


def test_the_newest_title_comes_first(tmp_path):
    path = tmp_path / "history.json"
    _record(path, key="a", title="Alpha", at=1.0)
    _record(path, key="b", title="Beta", at=2.0)
    assert [entry["title"] for entry in rv.recent(path)] == ["Beta", "Alpha"]


def test_opening_a_title_again_moves_it_to_the_front_once(tmp_path):
    path = tmp_path / "history.json"
    _record(path, key="a", title="Alpha", at=1.0)
    _record(path, key="b", title="Beta", at=2.0)
    _record(path, key="a", title="Alpha again", at=3.0)
    entries = rv.recent(path)
    assert [entry["title"] for entry in entries] == ["Alpha again", "Beta"]


def test_the_same_key_in_another_kind_is_a_different_title(tmp_path):
    path = tmp_path / "history.json"
    _record(path, kind="game", key="12", title="Game twelve", at=1.0)
    _record(path, kind="movie", key="12", title="Movie twelve", at=2.0)
    assert len(rv.recent(path)) == 2


def test_an_unknown_kind_is_refused_without_writing(tmp_path):
    path = tmp_path / "history.json"
    assert rv.record(path, kind="book", key="a", title="Alpha") is None
    assert not path.exists()


def test_a_broken_file_reads_as_an_empty_history(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not json", encoding="utf-8")
    assert rv.load(path) == []
    _record(path, key="a", title="Alpha", at=1.0)
    assert [entry["title"] for entry in rv.recent(path)] == ["Alpha"]


def test_untrustworthy_records_are_dropped(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        '{"entries": ['
        '{"kind": "game", "key": "ok", "title": "Kept", "viewed_at": 2},'
        '{"kind": "game", "key": "", "title": "No key", "viewed_at": 3},'
        '{"kind": "game", "key": "bad", "title": "Bad time", "viewed_at": "soon"},'
        '"nonsense"'
        "]}",
        encoding="utf-8",
    )
    assert [entry["title"] for entry in rv.load(path)] == ["Kept"]


def test_only_web_posters_are_kept(tmp_path):
    path = tmp_path / "history.json"
    _record(path, poster_url="file:///etc/passwd")
    assert rv.recent(path)[0]["poster_url"] == ""
    _record(path, key="b", poster_url="https://example.test/p.jpg")
    assert rv.recent(path)[0]["poster_url"] == "https://example.test/p.jpg"


def test_the_history_is_capped(tmp_path):
    path = tmp_path / "history.json"
    for index in range(rv.MAX_ENTRIES + 5):
        _record(path, key=f"k{index}", title=f"Title {index}", at=float(index))
    entries = rv.load(path)
    assert len(entries) == rv.MAX_ENTRIES
    assert entries[0]["title"] == f"Title {rv.MAX_ENTRIES + 4}"


def test_forget_and_clear(tmp_path):
    path = tmp_path / "history.json"
    _record(path, key="a", at=1.0)
    _record(path, key="b", at=2.0)
    assert rv.forget(path, "game", "a") is True
    assert rv.forget(path, "game", "a") is False
    assert [entry["key"] for entry in rv.load(path)] == ["b"]
    rv.clear(path)
    assert rv.load(path) == []
