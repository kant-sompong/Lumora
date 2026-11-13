# Lumora 0.6.20

Kryoto links are checked the way the Linux dialog checks them.

## Fixed

- **A link is proven before it is downloaded.** "Download link" on Windows put the picked link straight into the download queue; a mirror page that answers 200 is not a file, so it could queue an HTML page. It now probes the link first and queues it only when the server actually answered with a file — otherwise it says so and leaves the link to "Open in browser".
- **New "Check link" button.** Probes the selected link, records what came back (status, kind, filename, size), saves it in the catalogue and refreshes the row, so the next "Download link" is instant.
- **"Download all links"** queues only links already proven to be files and reports how many were skipped for a check.

## About the link count

A Kryoto record usually shows three links, and that is the provider'"'"'s own number: `/api/games/{slug}/downloads` returns 3, 3, 3, 5, 3, 3 links across a sample of six records. Windows is not reading fewer of them than Linux. A Linux catalogue that shows more has accumulated links from earlier imports, which are kept rather than removed.

## Notes

- Windows portable build for testing: unzip `Lumora-Windows-x64.zip` anywhere and run `Lumora.exe`. Nothing is installed; settings, library and catalogue live in `%APPDATA%\Lumora` and are shared with an installed copy on the same account.

## Known issues

- The Linux video freeze after a pause or a 10-second skip is still open.
- Streaming providers are third-party services and change without notice.
