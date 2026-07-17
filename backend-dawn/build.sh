#!/usr/bin/env bash
# Build the backend-dawn jar. Needs JAVA_HOME pointing at GraalVM/JDK 21.
#
# The Dawn toolchain is resolved by ../scripts/fetch-dawn.sh from .dawn-version at
# the repo root, so this laptop and CI compile with the same compiler. Set
# DAWN_BIN to point at a checkout instead when working on the language itself.
#
# Third-party jars are declared in dawn.toml's [java-deps] — `dawn` fetches them
# from Maven and copies them into lib/ next to the jar. The jar's manifest
# Class-Path references lib/ by relative path, so keep lib/ next to the jar when
# deploying. Set $DAWN_MAVEN_MIRROR to fetch from a closer mirror.
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-backend-dawn.jar}"

DAWN="$(../scripts/fetch-dawn.sh)"

"$DAWN" test .
"$DAWN" build . -o "$OUT"
echo "built $OUT with $("$DAWN" --version)"
