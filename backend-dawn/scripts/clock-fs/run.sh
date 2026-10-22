#!/usr/bin/env bash
# Prove source controls red before accepting real JVM and native primitives.
set -euo pipefail
HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$HERE/run.py" "$@"
