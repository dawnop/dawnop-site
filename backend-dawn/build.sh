#!/usr/bin/env bash
# Build the backend-dawn jar. Requires `dawn` on PATH (dawn-lang toolchain) and
# JAVA_HOME pointing at GraalVM/JDK 21. The jar's manifest Class-Path references
# the lib/ jars by relative path, so keep lib/ next to the jar when deploying.
set -euo pipefail
cd "$(dirname "$0")"

CP="lib/sqlite-jdbc-3.36.0.3.jar:lib/jbcrypt-0.4.jar"
OUT="${1:-backend-dawn.jar}"

dawn test --cp "$CP" .
dawn build --cp "$CP" . -o "$OUT"
echo "built $OUT"
