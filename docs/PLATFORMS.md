# Platform notes

Both editions share the same core (`lgl/`), so behaviour differs only where the
platform forces it. This page lists those differences instead of hiding them.

| Area | Linux (GTK 4) | Windows (Qt 6) |
| --- | --- | --- |
| Frontend | `lgl/ui.py`, `lgl/gtk_tools.py` | `lgl/qt_main.py`, `lgl/qt_tools.py` |
| Search shortcut | `Ctrl+F` | `Ctrl+F` |
| Settings storage | XDG config dir, keyring via Secret Service when available | AppData, Windows Credential Manager when available |
| Desktop notifications | `notify-send` | Not wired yet: `platform_support.notify()` returns `False`, so nothing is announced and nothing breaks |
| Opening files and folders | `xdg-open` | `os.startfile` |
| Download engine | bundled HTTP + BitTorrent | identical |
| Player | system default | system default |

## Playback

Lumora does not bundle a video player. It opens the system player, which means
codec support is whatever the operating system has:

- `.mkv` and `.avi` files may need extra codecs on a fresh Windows install.
- HDR and some 10-bit encodes depend on the player, not Lumora.
- DRM-protected titles cannot be played at all.

`playback.unsupported_format()` turns this into a sentence rather than a silent
failure, and `playback.actions()` offers "Show in folder" so the same file can be
tried in another player.

## Accessibility

The shortcuts table (`lgl/shortcuts.py`) carries both accelerator spellings, and
`lgl.shortcuts.contrast_ratio()` checks palette pairs against WCAG AA before a
theme ships. System scaling is respected by both toolkits automatically; no
fixed pixel sizes are set for text.

## Testing today

- Core logic: covered by the pytest suite on both platforms in CI.
- Qt frontend: tests run against a real Qt build on Windows and on Linux with
  `QT_QPA_PLATFORM=offscreen`.
- GTK frontend: CI imports it and runs its dialog tests on Linux; it needs a real
  GTK runtime, which the test host provides.
