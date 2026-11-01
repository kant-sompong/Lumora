# Lumora — Windows README

This is the Windows portable build of Lumora.

## Requirements
- Windows 10 (version 1809 or later) or Windows 11
- No Python installation required — the executable is self-contained.

## Running
1. Extract the ZIP archive to a folder of your choice.
2. Run `Lumora.exe`.

## Data locations
| Data | Path |
|------|------|
| Settings | `%APPDATA%\NativeGameLibrary\config.json` |
| Library index | `%LOCALAPPDATA%\NativeGameLibrary\library.json` |
| Download queue | `%LOCALAPPDATA%\NativeGameLibrary\downloads.json` |
| Cache | `%LOCALAPPDATA%\NativeGameLibrary\Cache\` |

The app was renamed to Lumora in 0.2.6, but it keeps using the `NativeGameLibrary` folders above so existing settings and libraries carry over.
| Downloads | User-chosen folder (Settings → Download folder) |

## Features
- Browse and search the Internet Archive game collection
- Persistent download queue with pause/resume/retry
- Local library with favorites, duplicates, integrity verification
- Provider API keys (RAWG, TMDB, MyAnimeList) stored in Windows Credential Manager
- Movies & TV tab (opens streaming providers in the system browser)
- Anime tab with MAL ranking and episode selection

## Building from source
On Windows 10/11 with Python 3.12+:

```powershell
python -m pip install pyinstaller pillow
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Optional: add `-BundleAria2` to include `aria2c.exe` for magnet link support.

## Troubleshooting
- **SmartScreen warning**: The executable is unsigned. Click "More info" → "Run anyway".
- **Missing DLLs**: All required DLLs are bundled. If something is missing, re-extract the ZIP.
- **Antivirus false positive**: Some heuristics flag PyInstaller builds. This is a known issue; the build is clean.

## License
MIT License — see the main repository README.