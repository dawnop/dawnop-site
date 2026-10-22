#!/usr/bin/env bash
# Run only loopback peers, with the same consumer linked to both owners.
set -euo pipefail
HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$HERE/run.py" "$@"
