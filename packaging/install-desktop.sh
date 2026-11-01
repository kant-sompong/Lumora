#!/bin/sh
# Install a per-user application-launcher entry. No root, no systemd.
# Re-run this after moving the project directory.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$DATA_DIR/applications"
ICON_DIR="$DATA_DIR/icons/hicolor"
ENTRY="$APPS_DIR/io.github.nativegamelibrary.NativeGameLibrary.desktop"

mkdir -p "$APPS_DIR"
chmod +x "$ROOT/run.sh"

# Install the PNG icons into the per-user hicolor theme so the launcher is not
# stuck with a generic icon. Missing icons are skipped, never fatal.
for size in 48 64 128 256; do
    source_png="$ROOT/packaging/linux/icons/native-game-library-$size.png"
    if [ -f "$source_png" ]; then
        target_dir="$ICON_DIR/${size}x${size}/apps"
        mkdir -p "$target_dir"
        cp "$source_png" "$target_dir/io.github.nativegamelibrary.NativeGameLibrary.png"
    fi
done
if [ -f "$ROOT/packaging/linux/io.github.nativegamelibrary.NativeGameLibrary.svg" ]; then
    mkdir -p "$ICON_DIR/scalable/apps"
    cp "$ROOT/packaging/linux/io.github.nativegamelibrary.NativeGameLibrary.svg" \
        "$ICON_DIR/scalable/apps/io.github.nativegamelibrary.NativeGameLibrary.svg"
fi

# Desktop Entry Exec quoting: wrap the path in double quotes so spaces work.
# Inside a double-quoted Exec value a literal backslash must be escaped, which
# matters for paths that themselves contain a backslash.
SAFE_ROOT=$(printf '%s' "$ROOT" | sed 's/\\/\\\\/g; s/"/\\"/g')
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Native Game Library
GenericName=Game Library
Comment=Browse and download Linux games from the Internet Archive
Exec="$SAFE_ROOT/run.sh"
Path=$SAFE_ROOT
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
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t "$ICON_DIR" >/dev/null 2>&1 || true
fi

printf 'Installed launcher entry: %s\n' "$ENTRY"