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
#   v0.1.0   a released tag — downloads that release's compiler jar (cached under .dawn/)
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
SUMS="$ROOT/.dawn-version.sha256"

if [ -n "${DAWN_BIN:-}" ]; then
  echo "using DAWN_BIN=$DAWN_BIN (unverified — not the pinned release)" >&2
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

# ---- integrity: the jar must be the one .dawn-version.sha256 names ----
# `--version` below asks the jar what it is; a substituted jar answers just as
# confidently. This is the half that asks whether it is the *same* jar.
sha256_of() {
  if command -v sha256sum > /dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum > /dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    echo ""
  fi
}

# Verify $1 (a jar) against the recorded hash for tag $2. Fatal on mismatch and
# on an unrecorded tag: a bump that forgets the checksum must break loudly, or
# "forgot to record it" silently becomes "not checked".
verify_jar() {
  local file=$1 tag=$2 want got
  if [ ! -f "$SUMS" ]; then
    # An old commit predating this file — nothing to check against, say so
    # rather than imply it passed.
    echo "!!! no $SUMS; $tag unverified" >&2
    return 0
  fi
  want="$(awk -v tag="$tag" '$1 !~ /^#/ && $2 == tag { print $1; exit }' "$SUMS")"
  got="$(sha256_of "$file")"
  if [ -z "$got" ]; then
    echo "!!! no sha256sum/shasum on PATH; $tag unverified" >&2
    return 0
  fi
  if [ -z "$want" ]; then
    echo "!!! $tag has no recorded sha256 in .dawn-version.sha256" >&2
    echo "    Bumping .dawn-version means recording its checksum in the same commit." >&2
    echo "    Verify this jar came from the release, then add the line:" >&2
    echo "" >&2
    echo "        $got  $tag" >&2
    echo "" >&2
    exit 1
  fi
  if [ "$want" != "$got" ]; then
    echo "!!! $tag failed checksum verification" >&2
    echo "    expected $want" >&2
    echo "    actual   $got" >&2
    echo "    file     $file" >&2
    echo "    This compiler builds the code that serves production; refusing to use it." >&2
    echo "    If the pin was just bumped, update .dawn-version.sha256 too." >&2
    exit 1
  fi
}

# ---- escape hatch: track main ----
if [ "$VERSION" = "main" ]; then
  echo "!!! .dawn-version is 'main' — building the compiler from source." >&2
  echo "    This is not reproducible; pin a tag before merging." >&2
  echo "    skipping checksum verification (branch build — there is no fixed artifact)" >&2
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
  echo "fetching dawn $VERSION" >&2
  mkdir -p "$(dirname "$JAR")"
  # The release asset was renamed when the compiler became self-hosted: v0.8.0 and
  # up publish dawn-selfhost.jar, everything before it published dawn.jar. Try both
  # so any released tag stays a usable pin — the point of .dawn-version is that
  # checking out an old commit reproduces it, and that dies if only today's naming
  # resolves. Cached as dawn.jar either way; the shim and the probe below do not
  # care which name it arrived under.
  ok=""
  for asset in dawn-selfhost.jar dawn.jar; do
    URL="https://github.com/$REPO/releases/download/$VERSION/$asset"
    # to .part first: an interrupted download must not leave a truncated jar that
    # every later run then treats as cached and fails on in some unrelated way.
    if curl -fsSL --retry 3 -o "$JAR.part" "$URL"; then
      ok="$asset"
      break
    fi
    rm -f "$JAR.part"
  done
  if [ -z "$ok" ]; then
    echo "!!! could not download dawn-selfhost.jar or dawn.jar for $VERSION" >&2
    echo "    is $VERSION released? see https://github.com/$REPO/releases" >&2
    exit 1
  fi
  # verified before it is promoted into the cache, so a bad download cannot
  # become the copy every later run trusts
  verify_jar "$JAR.part" "$VERSION"
  mv "$JAR.part" "$JAR"
else
  # and again on every cache hit — .dawn/ is a writable directory on disk, and
  # the download-time check says nothing about what is in it now
  verify_jar "$JAR" "$VERSION"
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
