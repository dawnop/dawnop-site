#!/usr/bin/env bash
# Install a zero traffic candidate. Always invoke without proxy env prefixes.
set -euo pipefail
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
[[ $(id -u) == 0 ]] || { echo 'run as root' >&2; exit 1; }
[[ ${1:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'usage: deploy-native.sh <full-sha> [--undo]' >&2; exit 1; }
APP="/opt/dawnop-dawn"
PROXY_FILE="$APP/.deploy-proxy"
PROXY="${DEPLOY_PROXY-$(cat "$PROXY_FILE" 2>/dev/null || true)}"
PROXY=$(printf '%s' "$PROXY" | tr -d ' \t\n\r')
# Match deploy.sh: missing or unreachable proxy falls back to direct downloads.
# Do not export proxy variables: local health checks must stay local.
DL=(--proxy '')
# The child shell expands its positional parameter, not this shell.
# shellcheck disable=SC2016
if [[ -z "$PROXY" ]]; then
  echo 'no proxy configured; direct GitHub downloads may be slow' >&2
elif timeout 2 bash -c 'exec 3<>/dev/tcp/"${1%:*}"/"${1##*:}"' _ "$PROXY" 2>/dev/null; then
  DL=(--proxy "$PROXY" --noproxy '')
else
  echo 'proxy unreachable; direct GitHub downloads may be slow' >&2
fi
NATIVE_TMP=$(mktemp -d)
trap 'rm -rf "${NATIVE_TMP:?}"' EXIT

download() {
  local attempt code
  for attempt in 1 2 3; do
    if curl -qfsSL "${DL[@]}" --connect-timeout 10 --max-time 60 "$@"; then
      return 0
    else
      code=$?
      echo "GitHub download attempt $attempt/3: curl exit $code" >&2
    fi
    if [[ $attempt -lt 3 ]]; then sleep "$attempt"; fi
  done
  return "$code"
}

# This trusted entrypoint pins the helper's Git blob independently of downloads.
# Update the pin in the same commit whenever deploy_native.py changes. Invoke the
# entrypoint from the requested commit; older helper versions fail closed.
EXPECTED_HELPER_BLOB="66898a1db675dea120ad7b58ff23fc718aabeff1"
download "https://raw.githubusercontent.com/dawnop/dawnop-site/$1/backend-dawn/deploy/deploy_native.py" -o "$NATIVE_TMP/install.py"
python3 - "$NATIVE_TMP/install.py" "$EXPECTED_HELPER_BLOB" <<'PY'
import hashlib
import re
import sys
from pathlib import Path

content = Path(sys.argv[1]).read_bytes()
expected = sys.argv[2]
if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{40}", expected):
    sys.exit("invalid helper Git blob digest")
actual = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
if actual != expected:
    sys.exit("helper Git blob digest mismatch")
PY
python3 "$NATIVE_TMP/install.py" --sha "$1" "${@:2}"
