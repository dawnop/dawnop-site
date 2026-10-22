#!/usr/bin/env bash
# Fetch the native compiler matching the existing toolchain pin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d ' \t\n\r' < "$ROOT/.dawn-version")"
case "$VERSION" in
  v0.97.0) SHA=34317c6cf609b94ae6a0ca4f6381d17c620afd5e58b3639742f307c53e9fad48 ;;
  *) echo "No verified native compiler digest for $VERSION" >&2; exit 1 ;;
esac
BIN="$ROOT/.dawn/$VERSION/dawnc"
if [[ ! -f "$BIN" ]]; then
  mkdir -p "$(dirname "$BIN")"
  curl -fsSL --retry 3 -o "$BIN.part" \
    "https://github.com/dawnop/dawn-lang/releases/download/$VERSION/dawnc-linux-x86_64"
  printf '%s  %s\n' "$SHA" "$BIN.part" | sha256sum -c - >&2
  mv "$BIN.part" "$BIN"
fi
printf '%s  %s\n' "$SHA" "$BIN" | sha256sum -c - >&2
chmod +x "$BIN"
echo "$BIN"
