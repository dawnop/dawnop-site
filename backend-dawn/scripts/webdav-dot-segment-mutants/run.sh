#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
DAWN="${DAWN_BIN:-}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-webdav-dot-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

base_log="$work/base.log"
if ! "$DAWN" test "$BACKEND" >"$base_log" 2>&1; then
  cat "$base_log" >&2
  exit 1
fi
printf 'PASS  base WebDAV dot segment tests\n'

mutants=(
  drop-dot-check
  drop-dotdot-check
  reject-dot-containing-names
)
owners=(
  'dest_rel rejects one-dot segments after one decode'
  'dest_rel rejects two-dot segments after one decode'
  'dest_rel accepts names that merely contain dots'
)

for index in "${!mutants[@]}"; do
  mutant="${mutants[$index]}"
  owner="${owners[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$project/dawn.toml"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project/src/api/webdav.dawn"

  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$work/$mutant.jar" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    exit 1
  fi
  printf 'PASS  %s compiles\n' "$mutant"

  test_log="$work/$mutant.test.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  status=$?
  set -e
  if [[ $status -eq 0 ]]; then
    printf 'FAIL  %s did not turn its owner red\n' "$mutant" >&2
    cat "$test_log" >&2
    exit 1
  fi

  mapfile -t failures < <(sed -n 's/^FAIL  //p' "$test_log")
  if [[ ${#failures[@]} -ne 1 ]]; then
    printf 'FAIL  %s produced %s failing assertions, expected one\n' \
      "$mutant" "${#failures[@]}" >&2
    cat "$test_log" >&2
    exit 1
  fi

  actual="${failures[0]}"
  if [[ "$actual" != "$owner" && "$actual" != *" :: $owner" ]]; then
    printf 'FAIL  %s turned the wrong assertion red: %s\n' "$mutant" "$actual" >&2
    cat "$test_log" >&2
    exit 1
  fi
  printf 'PASS  %s uniquely turns red: %s\n' "$mutant" "$owner"
done
