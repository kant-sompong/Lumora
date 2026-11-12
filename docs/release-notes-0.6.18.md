# Lumora 0.6.18

The Windows window watches again, and its Kryoto catalogue has download links.

## Fixed

- **Watching inside the app works again on Windows.** Pressing Watch Now opened the player page with "Playback opens in your browser" instead of the video: Qt refuses to load its WebEngine module once the application object exists, and the Windows window asked for it after that point, so the import failed silently and the page fell back. WebEngine is now loaded before the application starts, with shared GL contexts allowed first, which the packaged Chromium plugin also needs. Watch, the season list and trailers all play in the window again, fullscreen included.
- **Kryoto records carry their download links on Windows.** The Kryoto import skipped the pass that reads each record's details, so imported rows were stored with no link at all and "Download from catalogue…" had nothing to show. The import now reads those links (it takes longer, and the progress line says which phase it is in).
- **Links can be opened, not just queued.** Each recorded link is listed with its address, and the dialog adds **Open in browser**, **Copy link** and **Download link** for the one you picked; "Download all links" keeps the old bulk action.

## If you imported Kryoto before this release

Those records still have no link stored. Run **☰ → Kryoto Catalog… → Import all** once more; the second pass reads only what is missing, so already-read records are skipped.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still open.
- Streaming providers are third-party services and change without notice.
- The Windows player was fixed by import order and covered by tests, not by playing an episode on a Windows machine in this build.
