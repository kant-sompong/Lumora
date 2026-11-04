import os
from pathlib import Path

import pytest

from lgl import local_media as lm


def make(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "name,title,year,season,episode",
    [
        ("The.Matrix.1999.1080p.BluRay.x264-GRP.mkv", "The Matrix", 1999, None, None),
        ("Show.Name.S02E05.720p.mkv", "Show Name", None, 2, 5),
        ("Show Name Season 3 Episode 7.mp4", "Show Name", None, 3, 7),
        ("Arrival (2016).mkv", "Arrival", 2016, None, None),
        ("plain file.avi", "plain file", None, None, None),
    ],
)
def test_parse_name(name, title, year, season, episode):
    assert lm.parse_name(name) == (title, year, season, episode)


def test_guard_refuses_system_and_home_directories(tmp_path):
    ok, why = lm.guard_root("/")
    assert ok is False and "system" in why
    ok, why = lm.guard_root(Path.home())
    assert ok is False and "home" in why
    ok, why = lm.guard_root(tmp_path / "missing")
    assert ok is False
    target = tmp_path / "Movies"
    target.mkdir()
    assert lm.guard_root(target) == (True, "")


def test_filesystem_root_is_refused_by_structure_not_by_name():
    """Windows drive roots do not spell themselves "c:/" after normalisation."""
    root = Path(os.path.abspath(os.sep))
    assert root.parent == root, "expected a real filesystem root"
    ok, why = lm.guard_root(root)
    assert ok is False and "system" in why


def test_this_machines_system_directories_are_refused(tmp_path, monkeypatch):
    fake_windows = tmp_path / "Windows"
    fake_windows.mkdir()
    monkeypatch.setenv("SystemRoot", str(fake_windows))
    ok, why = lm.guard_root(fake_windows)
    assert ok is False and "system" in why


def test_scan_finds_videos_subtitles_and_posters(tmp_path):
    make(tmp_path / "Movies" / "Heat.1995.mkv")
    make(tmp_path / "Movies" / "Heat.1995.en.srt")
    make(tmp_path / "Movies" / "poster.jpg")
    make(tmp_path / "Movies" / "notes.txt")
    result = lm.scan([tmp_path / "Movies"])
    assert len(result) == 1
    entry = result.files[0]
    assert entry.title == "Heat" and entry.year == 1995
    assert entry.subtitles and entry.subtitles[0].suffix == ".srt"
    assert entry.artwork is not None and entry.artwork.name == "poster.jpg"
    assert entry.size > 0


def test_scan_honours_exclusions(tmp_path):
    make(tmp_path / "Media" / "keep.mkv")
    make(tmp_path / "Media" / "Sample" / "dropped.mkv")
    make(tmp_path / "Media" / "trailer.mkv")
    result = lm.scan(
        [tmp_path / "Media"],
        exclude_dirs=("sample",),
        exclude_globs=("trailer*",),
    )
    assert [f.path.name for f in result.files] == ["keep.mkv"]
    assert result.skipped == 1


def test_scan_caps_file_count(tmp_path):
    for i in range(5):
        make(tmp_path / "Many" / f"Episode {i}.mkv")
    result = lm.scan([tmp_path / "Many"], max_files=2)
    assert len(result) == 2 and result.truncated is True


def test_scan_reports_refused_roots(tmp_path):
    result = lm.scan(["/"])
    assert result.files == []
    assert result.errors and "system" in result.errors[0]


def test_matching_prefers_title_and_year():
    files = [
        lm.MediaFile(Path("/m/heat.mkv"), title="Heat", year=1995),
        lm.MediaFile(Path("/m/unknown.mkv"), title="Totally Unknown"),
    ]
    candidates = [
        {"title": "Heat", "year": 1986},
        {"title": "Heat", "year": 1995},
    ]
    paired = lm.match(files, candidates)
    assert paired[0][1]["year"] == 1995
    assert paired[1][1] is None


def test_missing_detects_removed_files(tmp_path):
    present = [tmp_path / "a.mkv"]
    gone = tmp_path / "b.mkv"
    assert lm.missing([present[0], gone, Path("/x/c.mkv")], present) == [gone, Path("/x/c.mkv")]


def test_media_file_key_includes_episode():
    movie = lm.MediaFile(Path("/m/a.mkv"), title="Heat", year=1995)
    episode = lm.MediaFile(Path("/m/b.mkv"), title="Show", season=1, episode=2)
    assert movie.key == "heat|1995"
    assert episode.key == "show|s01e002"
    assert "path" in movie.as_dict()
