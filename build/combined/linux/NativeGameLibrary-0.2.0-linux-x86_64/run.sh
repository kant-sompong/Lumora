#!/bin/sh
# Launch Native Game Library from a checkout. No installation required.
set -eu
cd "$(dirname "$0")"
exec python3 -m lgl "$@"
