#!/usr/bin/env bash
# Build the actual entry and prove wire, teardown and initialization controls red.
set -euo pipefail
HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$HERE/run.py" "$@"
