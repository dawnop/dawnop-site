#!/usr/bin/env bash
# Fetch the native compiler matching the existing toolchain pin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(tr -d ' \t\n\r' < "$ROOT/.dawn-version")"
case "$VERSION" in
  v0.97.0) SHA=34317c6cf609b94ae6a0ca4f6381d17c620afd5e58b3639742f307c53e9fad48 ;;
  v0.98.0) SHA=f3c1a110c24fad4e5205f410927ef1d46f6bfbb32906188f74c58e87c440e137 ;;
  v0.99.0) SHA=4a722f24f587b15c6d0aa75fa6aa5ac172d7402e1dcc9aab14317b9a48e4e45f ;;
  v0.100.0) SHA=0eb01756b76d4abea6fe2891e5e5af718c54b313887730e346f2a9e5c6d5b9e2 ;;
  v0.104.0) SHA=e1506ef0aee7978a00fa07a0c2eebe565a2a35fc788128590d6106baeaf0186f ;;
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
