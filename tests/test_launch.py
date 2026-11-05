"""Tests for the reviewed-file launcher (``lgl/launch.py``).

The launcher is POSIX-only: it refuses to run as root, checks execute bits and
starts Windows builds through Wine.  The Windows frontends have their own
launcher, so these tests are skipped there; ``tests/test_qt_*`` cover them.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from lgl import launch as launch_module

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the POSIX launcher does not exist on Windows"
)

#: The launcher refuses to touch files as root, so those tests cannot run as root.
not_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="the launcher refuses to change permissions as root",
)


def library(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "library"
    game = root / "Demo Game"
    game.mkdir(parents=True)
    return root, game


def script(game: Path, name: str, first_line: str) -> Path:
    path = game / name
    path.write_text(f"{first_line}\necho hi\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# B2 -- a refused file must keep its mode
# --------------------------------------------------------------------------

@not_root
def test_make_executable_adds_the_user_execute_bit(tmp_path):
    root, game = library(tmp_path)
    target = game / "demo.appimage"
    target.write_bytes(b"\x7fELF" + b"0" * 64)

    launch_module.make_executable(target, root)

    assert target.stat().st_mode & stat.S_IXUSR


@not_root
def test_make_executable_refuses_before_changing_the_mode(tmp_path):
    root, game = library(tmp_path)
    target = game / "notes.bin"
    target.write_bytes(b"definitely not an executable")
    before = target.stat().st_mode

    with pytest.raises(launch_module.LaunchError):
        launch_module.make_executable(target, root)

    assert target.stat().st_mode == before


@not_root
def test_make_executable_will_not_leave_the_library(tmp_path):
    root, game = library(tmp_path)
    outside = tmp_path / "elsewhere.appimage"
    outside.write_bytes(b"\x7fELF" + b"0" * 64)

    with pytest.raises(launch_module.LaunchError):
        launch_module.make_executable(outside, root)

    assert not outside.stat().st_mode & stat.S_IXUSR


# --------------------------------------------------------------------------
# B3 -- the shebang decides the interpreter
# --------------------------------------------------------------------------

@not_root
def test_a_bash_script_is_run_by_bash(tmp_path):
    if shutil.which("bash") is None and not Path("/bin/bash").exists():
        pytest.skip("bash is not installed here")
    root, game = library(tmp_path)
    target = script(game, "start.sh", "#!/bin/bash")

    plan = launch_module.plan_launch(target, root)

    assert plan.kind == "shell installer"
    assert Path(plan.command[0]).name.startswith("bash")
    assert plan.command[1:] == [str(target)]


@not_root
def test_a_bash_script_found_through_env_is_run_by_bash(tmp_path):
    if shutil.which("bash") is None and not Path("/bin/bash").exists():
        pytest.skip("bash is not installed here")
    root, game = library(tmp_path)
    target = script(game, "run.sh", "#!/usr/bin/env bash")

    plan = launch_module.plan_launch(target, root)

    assert Path(plan.command[0]).name.startswith("bash")


@not_root
def test_a_plain_script_still_uses_sh(tmp_path):
    root, game = library(tmp_path)
    target = script(game, "install.sh", "#!/bin/sh")

    plan = launch_module.plan_launch(target, root)

    assert plan.command == [launch_module.SHELL_INTERPRETER, str(target)]


# --------------------------------------------------------------------------
# L3 -- Windows builds: Wine, or a message that says so
# --------------------------------------------------------------------------

@not_root
def test_a_windows_build_says_wine_is_needed(tmp_path, monkeypatch):
    root, game = library(tmp_path)
    target = game / "game.exe"
    target.write_bytes(b"MZ" + b"0" * 64)
    monkeypatch.setattr(launch_module, "_wine", lambda: None)

    with pytest.raises(launch_module.LaunchError) as error:
        launch_module.plan_launch(target, root)

    assert "Wine" in str(error.value)


@not_root
def test_a_windows_build_runs_through_wine_when_it_is_installed(tmp_path, monkeypatch):
    root, game = library(tmp_path)
    target = game / "game.exe"
    target.write_bytes(b"MZ" + b"0" * 64)
    monkeypatch.setattr(launch_module, "_wine", lambda: "/usr/bin/wine")

    plan = launch_module.plan_launch(target, root)

    assert plan.kind == "Windows build"
    assert plan.command == ["/usr/bin/wine", str(target)]


@not_root
def test_a_windows_batch_file_is_run_through_cmd(tmp_path, monkeypatch):
    root, game = library(tmp_path)
    target = game / "start.bat"
    target.write_bytes(b"@echo off\n")
    monkeypatch.setattr(launch_module, "_wine", lambda: "/usr/bin/wine")

    plan = launch_module.plan_launch(target, root)

    assert plan.command == ["/usr/bin/wine", "cmd", "/c", str(target)]


# --------------------------------------------------------------------------
# L1 -- a log is never a reason to refuse a launch
# --------------------------------------------------------------------------

@not_root
def test_the_launch_log_falls_back_to_the_data_folder(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    game.chmod(0o500)
    data = tmp_path / "data"
    monkeypatch.setattr("lgl.platform_support.data_dir", lambda *a, **k: data)
    handle = None
    try:
        handle, path = launch_module.open_launch_log(game)
        assert handle is not None
        assert path is not None
        assert path.parent == data / "launch-logs"
        assert path.name == "game.log"
    finally:
        if handle is not None:
            handle.close()
        game.chmod(0o700)


@not_root
def test_the_launch_log_uses_the_game_folder_when_it_can(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr("lgl.platform_support.data_dir", lambda *a, **k: tmp_path / "data")
    handle = None
    try:
        handle, path = launch_module.open_launch_log(game)
        assert handle is not None
        assert path == game / "launch.log"
    finally:
        if handle is not None:
            handle.close()


def test_a_windows_build_runs_directly_on_windows(tmp_path, monkeypatch):
    from lgl import launch

    monkeypatch.setattr(launch, "_on_windows", lambda: True)
    target = tmp_path / "Game.exe"
    target.write_bytes(b"MZ")
    assert launch.plan_launch(target, tmp_path).command == [str(target)]


def test_a_batch_file_uses_comspec_on_windows(tmp_path, monkeypatch):
    from lgl import launch

    monkeypatch.setattr(launch, "_on_windows", lambda: True)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    target = tmp_path / "play.bat"
    target.write_text("@echo off\n", encoding="utf-8")
    plan = launch.plan_launch(target, tmp_path)
    assert plan.command[0].endswith("cmd.exe")
    assert plan.command[1] == "/c"


def test_is_root_is_false_where_geteuid_is_missing(monkeypatch):
    import os as os_module

    from lgl import launch

    monkeypatch.delattr(os_module, "geteuid", raising=False)
    assert launch._is_root() is False
