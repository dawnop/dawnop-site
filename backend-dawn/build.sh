#!/usr/bin/env bash
# Build the backend-dawn jar. Requires `dawn` on PATH (dawn-lang toolchain) and
# JAVA_HOME pointing at GraalVM/JDK 21.
#
# Third-party jars are declared in dawn.toml's [java-deps] — `dawn` fetches them
# from Maven and copies them into lib/ next to the jar. The jar's manifest
# Class-Path references lib/ by relative path, so keep lib/ next to the jar when
# deploying. Set $DAWN_MAVEN_MIRROR to fetch from a closer mirror.
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-backend-dawn.jar}"

dawn test .
dawn build . -o "$OUT"
echo "built $OUT"
