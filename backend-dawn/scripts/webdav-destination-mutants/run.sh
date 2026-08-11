#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
MATRIX_CHECK="$HERE/matrix_check.py"
DAWN="${DAWN_BIN:-}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-webdav-destination-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

python3 "$MATRIX_CHECK" --self-test
python3 "$MUTATOR" --list >"$work/mutants.txt"
python3 "$MATRIX_CHECK" --matrix "$MATRIX" --mutants "$work/mutants.txt"
if python3 "$MUTATOR" unknown-mutant "$BACKEND" >"$work/unknown.log" 2>&1; then
  printf 'FAIL  mutate.py accepted an unknown mutant\n' >&2
  exit 1
fi
printf 'PASS  mutate.py rejects unknown mutants\n'

base_log="$work/base.log"
if ! "$DAWN" test "$BACKEND" >"$base_log" 2>&1; then
  cat "$base_log" >&2
  exit 1
fi
printf 'PASS  base WebDAV Destination assertions\n'

declare -a mutants=()
declare -a owners=()
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* || "$line" == version=* ]] && continue
  mutants+=("${line%%|*}")
  owners+=("${line#*|}")
done <"$MATRIX"

for index in "${!mutants[@]}"; do
  mutant="${mutants[$index]}"
  owner="${owners[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$BACKEND/dawn.lock" "$project/"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$work/$mutant.jar" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    exit 1
  fi
  if [[ ! -s "$work/$mutant.jar" ]]; then
    printf 'FAIL  %s produced no jar\n' "$mutant" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

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
