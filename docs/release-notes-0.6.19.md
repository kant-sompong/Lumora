# Lumora 0.6.19

The Kryoto import tells you how far it has got.

## Changed

- **A number while details are fetched.** After the list is read, the import reads every record's details and download links, and that pass could sit on "Fetching details…" for a long time with nothing moving. The status line now reads `Fetching details 120 / 900 (13%) · 118 records read · 240 download link(s)`, and the progress bar becomes a real measure of that pass instead of a spinning bar.
- Failures are counted in the same line, so a record the provider refuses does not look like a stall.

## Notes

- This is the Windows portable build for testing: unzip `Lumora-Windows-x64.zip` anywhere and run `Lumora.exe`. Nothing is installed and no settings are shared with the installed copy unless you point it at the same folder.
- The detail pass still takes as long as the provider takes — roughly a second per record with four requests in flight — so a large catalogue is a minutes-long import, not a stuck one.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still open.
- Streaming providers are third-party services and change without notice.
