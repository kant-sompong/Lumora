# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

Built on a real Windows runner (GitHub Actions ``windows-latest``); a binary
cross-built on Linux is not a supported or tested artifact.

    pyinstaller --noconfirm packaging/windows/NativeGameLibrary.spec

Output: ``dist/NativeGameLibrary/NativeGameLibrary.exe`` (one directory).
The directory is what gets zipped into the portable distribution, because
one-file builds are slower to start and are more often quarantined by
Defender's heuristics.
"""

from pathlib import Path

SPEC_ROOT = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_ROOT.parent.parent
ICON = SPEC_ROOT / "icon.ico"

ARIA2 = SPEC_ROOT / "tools" / "aria2c.exe"
ARIA2_LICENSE = SPEC_ROOT / "tools" / "ARIA2-COPYING.txt"

binaries = []
if ARIA2.exists():
    binaries.append((str(ARIA2), "."))

datas = [
    (str(PROJECT_ROOT / "lgl" / "style.css"), "lgl"),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(SPEC_ROOT / "README-Windows.md"), "."),
]
if ARIA2_LICENSE.exists():
    datas.append((str(ARIA2_LICENSE), "."))

a = Analysis(
    [str(SPEC_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "lgl.windows_app",
        "lgl.platform_support",
        "lgl.settings",
        "lgl.archive",
        "lgl.downloader",
        "lgl.library",
        "lgl.library_store",
        "lgl.integrity",
        "lgl.compatibility",
        "lgl.torrent",
        "lgl.magnet",
        "lgl.bittorrent",
        "lgl.bencode",
        "lgl.launch",
        "lgl.filetypes",
        "lgl.util",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "PIL.Image",
        "PIL.ImageTk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "PySide6", "PyQt6", "gi"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NativeGameLibrary",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NativeGameLibrary",
)