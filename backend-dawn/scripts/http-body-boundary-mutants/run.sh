#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
FFI_CHECK="$BACKEND/scripts/check-ffi-boundaries.py"
DAWN="${DAWN_BIN:-}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-http-body-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

base_test="$work/base.test.log"
if ! "$DAWN" test "$BACKEND" >"$base_test" 2>&1; then
  cat "$base_test" >&2
  exit 1
fi
python3 "$FFI_CHECK" --root "$BACKEND"
printf 'PASS  base HTTP request body assertions\n'

declare -a mutants=()
declare -a owners=()
while IFS='|' read -r mutant owner; do
  [[ -z "$mutant" || "$mutant" == \#* ]] && continue
  if [[ -z "$owner" ]]; then
    printf 'FAIL  matrix entry has no owner: %s\n' "$mutant" >&2
    exit 1
  fi
  mutants+=("$mutant")
  owners+=("$owner")
done <"$MATRIX"

expected_mutants=(swap-file-tail drop-upload-prefix leak-java-net-http)
if [[ "${mutants[*]}" != "${expected_mutants[*]}" ]]; then
  printf 'FAIL  matrix mutant order or membership changed\n' >&2
  exit 1
fi

for index in "${!mutants[@]}"; do
  mutant="${mutants[$index]}"
  owner="${owners[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$project/dawn.toml"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$work/$mutant.jar" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

  test_log="$work/$mutant.test.log"
  ffi_log="$work/$mutant.ffi.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  test_status=$?
  python3 "$FFI_CHECK" --root "$project" >"$ffi_log" 2>&1
  ffi_status=$?
  set -e

  mapfile -t test_failures < <(sed -n 's/^FAIL  //p' "$test_log")
  mapfile -t ffi_failures < <(sed -n 's/^FAIL  //p' "$ffi_log")
  failures=("${test_failures[@]}" "${ffi_failures[@]}")

  if [[ $test_status -eq 0 && $ffi_status -eq 0 ]]; then
    printf 'FAIL  %s did not turn its owner red\n' "$mutant" >&2
    exit 1
  fi
  if [[ ${#failures[@]} -ne 1 ]]; then
    printf 'FAIL  %s produced %s red assertions, expected one\n' \
      "$mutant" "${#failures[@]}" >&2
    cat "$test_log" >&2
    cat "$ffi_log" >&2
    exit 1
  fi

  actual="${failures[0]}"
  if [[ "$actual" != "$owner" && "$actual" != *" :: $owner" ]]; then
    printf 'FAIL  %s turned the wrong assertion red: %s\n' "$mutant" "$actual" >&2
    cat "$test_log" >&2
    cat "$ffi_log" >&2
    exit 1
  fi
  printf 'PASS  %s uniquely turns red: %s\n' "$mutant" "$owner"
done
