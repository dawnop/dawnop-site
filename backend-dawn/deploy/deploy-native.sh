#!/usr/bin/env bash
# Install a zero traffic candidate. Always invoke without proxy env prefixes.
set -euo pipefail
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
[[ $(id -u) == 0 ]] || { echo 'run as root' >&2; exit 1; }
[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'usage: deploy-native.sh <full-sha> [--undo]' >&2; exit 1; }
NATIVE_TMP=$(mktemp -d)
trap 'rm -rf "${NATIVE_TMP:?}"' EXIT
# A commit addressed helper, never a moving main URL. It performs all CI gates
# before any installation, and independently verifies the downloaded ZIP.
curl -qfsSL --max-time 60 "https://raw.githubusercontent.com/dawnop/dawnop-site/$1/backend-dawn/deploy/deploy_native.py" -o "$NATIVE_TMP/install.py"
python3 "$NATIVE_TMP/install.py" --sha "$1" "${@:2}"
