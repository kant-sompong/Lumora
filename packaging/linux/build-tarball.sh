#!/bin/sh
# Build a portable Linux source tarball (no compilation, no bundling of GTK).
#
#   sh packaging/linux/build-tarball.sh
#
# Produces dist/NativeGameLibrary-<version>-linux-<arch>.tar.gz containing the
# application, run.sh, icons, the desktop-entry installer, .env.example and the
# README.  PyGObject, GTK 3 and (for Movies & TV playback) WebKit2GTK come from
# the distribution, as documented in the README.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"

PYTHON=${PYTHON:-python3}
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "Python 3.10 or newer is required." >&2
    exit 1
}

VERSION=$("$PYTHON" -c 'import lgl; print(lgl.__version__)')
ARCH=$(uname -m)
NAME="NativeGameLibrary-$VERSION-linux-$ARCH"
STAGE="build/$NAME"

rm -rf "$STAGE"
mkdir -p "$STAGE" dist

cp -a lgl "$STAGE/lgl"
cp run.sh README.md .env.example "$STAGE/"
mkdir -p "$STAGE/packaging/linux/icons"
cp packaging/install-desktop.sh "$STAGE/packaging/"
cp packaging/linux/build-tarball.sh "$STAGE/packaging/linux/"
cp packaging/linux/io.github.nativegamelibrary.NativeGameLibrary.svg "$STAGE/packaging/linux/"
cp packaging/linux/icons/*.png "$STAGE/packaging/linux/icons/"

find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -delete

# Reproducible archive where the tools support it: stable ordering, ownership
# and timestamps taken from SOURCE_DATE_EPOCH (defaults to the last commit).
EPOCH=${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}
TAR_OPTS=""
if tar --help 2>/dev/null | grep -q -- '--sort'; then
    TAR_OPTS="--sort=name --owner=0 --group=0 --numeric-owner"
fi
if tar --help 2>/dev/null | grep -q -- '--mtime'; then
    TAR_OPTS="$TAR_OPTS --mtime=@$EPOCH"
fi

# shellcheck disable=SC2086 # word splitting is intended for the option list
tar -C build $TAR_OPTS -cf - "$NAME" | gzip -n -9 > "dist/$NAME.tar.gz"
echo "dist/$NAME.tar.gz"