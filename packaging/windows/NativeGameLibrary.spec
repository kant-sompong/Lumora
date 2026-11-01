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

import sysconfig

# Get tkinter path from the running Python
TKINTER_PATH = Path(sysconfig.get_path("stdlib")) / "tkinter"

# TCL/TK library paths (needed for runtime)
TCL_PATH = Path(sysconfig.get_path("stdlib")).parent / "tcl" / "tcl8.6"
TK_PATH = Path(sysconfig.get_path("stdlib")).parent / "tcl" / "tk8.6"

datas = [
    (str(PROJECT_ROOT / "lgl" / "style.css"), "lgl"),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(SPEC_ROOT / "README-Windows.md"), "."),
]
if ARIA2_LICENSE.exists():
    datas.append((str(ARIA2_LICENSE), "."))

# Force include tkinter pure Python modules
if TKINTER_PATH.exists():
    datas.append((str(TKINTER_PATH), "tkinter"))

# Force include TCL/TK library files (init.tcl, etc.)
if TCL_PATH.exists():
    datas.append((str(TCL_PATH), "tcl/tcl8.6"))
if TK_PATH.exists():
    datas.append((str(TK_PATH), "tcl/tk8.6"))

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
        "tkinter.colorchooser",
        "tkinter.font",
        "tkinter.scrolledtext",
        "tkinter.simpledialog",
        "tkinter.dnd",
        "_tkinter",
        "PIL.Image",
        "PIL.ImageTk",
    ],
    collect_all=["tkinter"],
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