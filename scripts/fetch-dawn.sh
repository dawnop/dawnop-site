#!/usr/bin/env bash
# Resolve the Dawn toolchain this repo is pinned to, and print the path to a
# runnable `dawn` on stdout. Everything else here goes to stderr, so callers can:
#
#   DAWN=$(scripts/fetch-dawn.sh) && "$DAWN" test backend-dawn
#
# The pin lives in .dawn-version at the repo root and is the single source of
# truth — CI and this laptop resolve the same file, so "works here, breaks in CI"
# cannot come from a compiler mismatch.
#
# .dawn-version accepts:
#   v0.1.0   a released tag — downloads that release's dawn.jar (cached under .dawn/)
#   main     the escape hatch — clones dawn-lang and builds it, for when you are
#            changing the language and the backend together and there is no tag yet.
#            Reproducibility is gone while this is set; do not commit it for long.
#
# Override for local compiler work, skipping all of the above:
#   DAWN_BIN=~/workspace/dawn-lang/bin/dawn ./backend-dawn/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="$ROOT/.dawn"
REPO="dawnop/dawn-lang"

if [ -n "${DAWN_BIN:-}" ]; then
  echo "using DAWN_BIN=$DAWN_BIN" >&2
  echo "$DAWN_BIN"
  exit 0
fi

VERSION="$(tr -d ' \t\n\r' < "$ROOT/.dawn-version")"
[ -n "$VERSION" ] || { echo "!!! .dawn-version is empty" >&2; exit 1; }

# Both paths below need a JDK 21, and neither should require the caller to have
# exported JAVA_HOME first: gradle's toolchain detection does not look in ~/tools,
# and CI sets JAVA_HOME while this laptop does not. Same probe the shim uses.
find_jdk() {
  [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ] && return 0
  for d in "$HOME"/tools/graalvm-*/Contents/Home "$HOME"/tools/graalvm-*; do
    if [ -x "$d/bin/java" ]; then
      JAVA_HOME="$d"
      export JAVA_HOME
      return 0
    fi
  done
  return 1
}

# ---- escape hatch: track main ----
if [ "$VERSION" = "main" ]; then
  echo "!!! .dawn-version is 'main' — building the compiler from source." >&2
  echo "    This is not reproducible; pin a tag before merging." >&2
  SRC="$CACHE/dawn-lang"
  if [ -d "$SRC/.git" ]; then
    git -C "$SRC" fetch --depth 1 origin main >&2
    git -C "$SRC" reset --hard origin/main >&2
  else
    mkdir -p "$CACHE"
    git clone --depth 1 "https://github.com/$REPO" "$SRC" >&2
  fi
  find_jdk || { echo "!!! no JDK 21 found; set JAVA_HOME" >&2; exit 1; }
  (cd "$SRC" && ./gradlew -q :compiler:fatJar >&2)
  echo "$SRC/bin/dawn"
  exit 0
fi

# ---- normal path: a pinned release ----
JAR="$CACHE/$VERSION/dawn.jar"
SHIM="$CACHE/$VERSION/dawn"

if [ ! -f "$JAR" ]; then
  URL="https://github.com/$REPO/releases/download/$VERSION/dawn.jar"
  echo "fetching dawn $VERSION" >&2
  mkdir -p "$(dirname "$JAR")"
  # to .part first: an interrupted download must not leave a truncated jar that
  # every later run then treats as cached and fails on in some unrelated way.
  curl -fsSL --retry 3 -o "$JAR.part" "$URL" || {
    echo "!!! could not download $URL" >&2
    echo "    is $VERSION released? see https://github.com/$REPO/releases" >&2
    rm -f "$JAR.part"
    exit 1
  }
  mv "$JAR.part" "$JAR"
fi

# The shim mirrors dawn-lang's own bin/dawn: find a JDK, then delegate.
if [ ! -x "$SHIM" ]; then
  cat > "$SHIM" <<'SHIMEOF'
#!/bin/sh
set -e
JAR="$(cd "$(dirname "$0")" && pwd)/dawn.jar"
if [ -z "$JAVA_HOME" ]; then
  for d in "$HOME"/tools/graalvm-*/Contents/Home "$HOME"/tools/graalvm-*; do
    if [ -x "$d/bin/java" ]; then JAVA_HOME="$d"; export JAVA_HOME; break; fi
  done
fi
if [ -n "$JAVA_HOME" ] && [ -x "$JAVA_HOME/bin/java" ]; then
  exec "$JAVA_HOME/bin/java" -jar "$JAR" "$@"
fi
exec java -jar "$JAR" "$@"
SHIMEOF
  chmod +x "$SHIM"
fi

# Verify rather than assume: a jar from a cache directory should be made to say
# what it is before it compiles the thing that serves production.
GOT="$("$SHIM" --version 2>/dev/null || true)"
case "$GOT" in
  "dawn ${VERSION#v} ("*) ;;
  *)
    echo "!!! $JAR reports '$GOT', expected dawn ${VERSION#v}" >&2
    echo "    delete $CACHE/$VERSION and retry" >&2
    exit 1
    ;;
esac

echo "$SHIM"
