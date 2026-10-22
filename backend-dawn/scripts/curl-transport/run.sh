#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
exec python3 backend-dawn/scripts/curl-transport/run.py "$@"
