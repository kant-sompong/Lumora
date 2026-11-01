#!/bin/sh
# Install a per-user application-launcher entry. No root, no systemd.
# Re-run this after moving the project directory.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ENTRY="$APPS_DIR/io.github.nativegamelibrary.NativeGameLibrary.desktop"

mkdir -p "$APPS_DIR"
chmod +x "$ROOT/run.sh"

# Desktop Entry Exec quoting: wrap the path in double quotes so spaces work.
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Native Game Library
GenericName=Game Library
Comment=Browse and download Linux games from the Internet Archive
Exec="$ROOT/run.sh"
Path=$ROOT
Icon=applications-games
Terminal=false
Categories=Game;
Keywords=games;archive;internet archive;linux;download;
StartupNotify=true
StartupWMClass=native-game-library
EOF

chmod 0644 "$ENTRY"
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$ENTRY"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

printf 'Installed launcher entry: %s\n' "$ENTRY"
